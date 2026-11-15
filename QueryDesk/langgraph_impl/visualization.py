"""Optional, bounded chart planning from authorized results; never execute SQL."""
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import math
import re
import threading
import time
from html import escape
import json
import base64
from io import BytesIO
import importlib.util

from .agents import VisualizationAgent
from .contracts import Budget,VisualizationOutput
from .value_codec import decode,dumps,encode


def number(value):
    if type(value) not in {int,float,Decimal}:return None
    try:
        result=float(value)
        if not math.isfinite(result) or abs(result)>2**53-1:return None
        if isinstance(value,Decimal) and Decimal(str(result))!=value:return None
        if type(value) is int and int(result)!=value:return None
        return result
    except (ValueError,OverflowError):return None


def date_value(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}(?:-\d{2}(?:T[\d:.+Z-]+)?)?',value):return None
    try:
        text=value+'-01' if len(value)==7 else value
        parsed=datetime.fromisoformat(text.replace('Z','+00:00'))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()
    except (ValueError,OverflowError,OSError):return None


def profile_result(result):
    result=decode(result)
    columns=result.get('columns',[]);rows=result.get('rows',[])
    if not isinstance(columns,list) or not isinstance(rows,list) or len(columns)>64 or len(rows)>1000:
        raise ValueError('Visualization supports at most 64 columns and 1,000 returned rows. Narrow the query.')
    if any(not isinstance(name,str) for name in columns) or any(not isinstance(row,list) or len(row)!=len(columns) for row in rows):raise ValueError('The result shape is invalid')
    fields=[]
    for index,name in enumerate(columns):
        values=[row[index] for row in rows if row[index] is not None]
        numeric=bool(values) and all(type(value) is int or (type(value) is float and math.isfinite(value)) or (isinstance(value,Decimal) and value.is_finite()) for value in values)
        temporal=bool(values) and all(date_value(value) is not None for value in values)
        identifier=bool(re.search(r'(^id$|_id$|phone|mobile|postal|zip)',name,re.I))
        kind='identifier' if identifier else 'numeric' if numeric else 'temporal' if temporal else 'category' if values and all(isinstance(value,str) for value in values) else 'unsupported'
        fields.append({'index':index,'name':name[:160],'kind':kind,'nulls':len(rows)-len(values),
            'distinct':len({dumps(value,sort_keys=True) for value in values}),
            'plot_safe':numeric and not identifier and all(number(value) is not None for value in values)})
    return result,{'row_count':len(rows),'truncated':bool(result.get('truncated')),'columns':fields}


def needs_data(reason,suggestions=None):
    return {'status':'needs_data','kind':'none','title':'Improve this result for a chart','reason':reason,
            'library':'none',
            'x_column':None,'y_column':None,'suggestions':suggestions or ['Add a grouping attribute such as month or region and an aggregated numeric metric.'],
            'row_indices':[],'alternatives':[],'warnings':[]}


def validate_plan(raw,result,profile):
    plan=VisualizationOutput.model_validate(raw).model_dump()
    if plan['status']=='needs_data':return {**plan,'row_indices':[],'alternatives':[],'warnings':[]}
    columns=profile['columns'];rows=result['rows'];kind=plan['kind'];x=plan['x_column'];y=plan['y_column']
    if y is None or not 0<=y<len(columns) or columns[y]['kind']!='numeric' or (kind!='kpi' and not columns[y]['plot_safe']):
        return needs_data('The selected metric is not a safely plottable numeric column. High-precision values and identifiers are not rounded into metrics.')
    if kind=='kpi':
        if len(rows)!=1 or rows[0][y] is None or x is not None:return needs_data('A KPI card requires exactly one row and one numeric metric, without an X axis.')
        indices=[0];alternatives=['kpi']
    else:
        if x is None or not 0<=x<len(columns) or x==y:return needs_data('A chart needs two different existing columns.')
        if kind=='scatter' and (columns[x]['kind']!='numeric' or not columns[x]['plot_safe']):return needs_data('Scatter plots need two numeric metrics; add a second metric to the result.')
        if kind in {'bar','line'} and columns[x]['kind'] not in {'category','temporal'}:return needs_data('Add a category or ISO date/time grouping column alongside the numeric metric.')
        if kind=='line' and columns[x]['kind']!='temporal':return needs_data('A time-series line needs an ISO date/time column; add a month or date grouping attribute.')
        indices=[index for index,row in enumerate(rows) if row[x] is not None and row[y] is not None]
        if len(indices)<2:return needs_data('At least two complete data points are needed. Add a grouping attribute or adjust filters.')
        if kind=='line':indices=[index for index,row in enumerate(rows) if row[x] is not None]
        values=[rows[index][x] for index in indices]
        if kind in {'bar','line'} and len({dumps(value) for value in values})!=len(values):return needs_data('Repeated X-axis values need explicit aggregation or an additional grouping attribute. No rows were silently combined.')
        if len(indices)>(30 if kind=='bar' else 200 if kind=='line' else 500):return needs_data('Too many points for this chart. Request a narrower period, fewer groups, or an explicit top-N report.')
        if kind=='line':
            keys=[date_value(value) for value in values]
            if len(set(keys))!=len(keys):return needs_data('Multiple rows represent the same date/time. Aggregate them in a new reviewed query.')
            indices.sort(key=lambda index:date_value(rows[index][x]))
        alternatives=[kind]
        if kind in {'bar','line'} and columns[x]['kind']=='temporal' and len(indices)<=30:alternatives=['bar','line']
    warnings=[]
    if result.get('truncated'):warnings.append('This chart represents only returned data; the query result was limited or clipped.')
    if len(indices)!=len(rows):warnings.append(f'{len(rows)-len(indices)} rows with NULL chart coordinates are omitted. NULLs are not converted to zero.')
    if kind=='line' and any(row[y] is None for row in rows):warnings.append('NULL metrics appear as gaps in the line, not zero values.')
    if kind!='kpi':warnings.append('Chart geometry uses plotting numbers. Exact values remain in the table and in interactive tooltips where available.')
    return {**plan,'row_indices':indices,'alternatives':alternatives,'warnings':warnings}


def render_figure(plan,result):
    """Build only trusted Plotly traces. The LLM never supplies figure JSON or code."""
    if plan['status']!='chart':return {**plan,'figure':None,'kpi':None}
    from .value_codec import display
    y=plan['y_column'];x=plan['x_column'];rows=[result['rows'][index] for index in plan['row_indices']]
    if plan['kind']=='kpi':return {**plan,'figure':None,'kpi':{'label':result['columns'][y],'value':display(rows[0][y])}}
    import plotly.graph_objects as go
    xs=[number(row[x]) if plan['kind']=='scatter' else escape(str(row[x])) for row in rows]
    ys=[number(row[y]) for row in rows]
    exact=[[escape(display(row[x])),escape(display(row[y]))] for row in rows]
    common={'x':xs,'y':ys,'customdata':exact,'hovertemplate':'%{customdata[0]}<br><b>%{customdata[1]}</b><extra></extra>','name':escape(result['columns'][y][:160])}
    if plan['kind']=='bar':trace=go.Bar(**common,marker={'color':'#168579','line':{'color':'#0e5f57','width':1}},opacity=.92)
    elif plan['kind']=='line':trace=go.Scatter(**common,mode='lines+markers',line={'color':'#7159c1','width':3},marker={'size':8,'color':'#7159c1'},connectgaps=False)
    else:trace=go.Scatter(**common,mode='markers',marker={'size':12,'color':ys,'colorscale':'Viridis','opacity':.8,'line':{'color':'white','width':1}})
    figure=go.Figure(trace)
    figure.update_layout(template='plotly_white',showlegend=False,height=420,
        paper_bgcolor='rgba(0,0,0,0)',plot_bgcolor='rgba(0,0,0,0)',
        font={'family':'Arial, sans-serif','color':'#254838'},margin={'l':65,'r':24,'t':25,'b':90},
        xaxis={'title':{'text':escape(result['columns'][x][:160])},'automargin':True,'type':'date' if plan['kind']=='line' else 'linear' if plan['kind']=='scatter' else 'category'},
        yaxis={'title':{'text':escape(result['columns'][y][:160])},'automargin':True,'rangemode':'tozero' if plan['kind']=='bar' else 'normal'})
    raw=figure.to_json()
    if len(raw.encode())>262144:return needs_data('The chart payload is too large. Select fewer points or shorter grouping labels.')
    return {**plan,'figure':json.loads(raw),'kpi':None}


class PlotlyRenderer:
    """Renderer boundary: additional Python libraries need their own tested adapters."""
    name='plotly'
    kinds=('bar','line','scatter','kpi')
    interactive=True
    def render(self,plan,result):return render_figure(plan,result)


_STATIC_LOCK=threading.Lock()


class StaticRenderer:
    interactive=False
    def __init__(self,name):
        if name not in {'matplotlib','seaborn'}:raise ValueError('Unsupported static renderer')
        self.name=name
        self.kinds=('bar','scatter','kpi') if name=='seaborn' else ('bar','line','scatter','kpi')

    def render(self,plan,result):
        if plan['status']!='chart' or plan['kind']=='kpi':return render_figure(plan,result)
        import os
        from .config import APP_DIR
        os.environ.setdefault('MPLCONFIGDIR',str(APP_DIR/'outputs'/'matplotlib'))
        with _STATIC_LOCK:
            import matplotlib
            matplotlib.use('Agg')
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            from .value_codec import display
            with matplotlib.rc_context({'text.usetex':False,'text.parse_math':False,'font.family':'DejaVu Sans'}):
                figure=Figure(figsize=(9,4.2),dpi=130,layout='constrained');FigureCanvasAgg(figure)
                axis=figure.subplots();x=plan['x_column'];y=plan['y_column']
                rows=[result['rows'][index] for index in plan['row_indices']]
                labels=[display(row[x]) for row in rows];ys=[number(row[y]) for row in rows]
                xs=[number(row[x]) for row in rows] if plan['kind']=='scatter' else [label[:40]+('…' if len(label)>40 else '') for label in labels]
                try:
                    if self.name=='seaborn':
                        import seaborn as sns
                        if plan['kind']=='bar':
                            positions=list(range(len(xs)))
                            sns.barplot(x=positions,y=ys,ax=axis,errorbar=None,color='#218c7c',order=positions)
                            axis.set_xticks(positions,xs)
                        else:sns.scatterplot(x=xs,y=ys,hue=ys,palette='viridis',s=85,legend=False,ax=axis)
                    elif plan['kind']=='bar':axis.bar(range(len(xs)),ys,color='#218c7c');axis.set_xticks(range(len(xs)),xs)
                    elif plan['kind']=='line':
                        from matplotlib.dates import AutoDateLocator,ConciseDateFormatter
                        dates=[datetime.fromtimestamp(date_value(row[x]),timezone.utc) for row in rows]
                        axis.plot(dates,ys,color='#7159c1',linewidth=2.5,marker='o')
                        locator=AutoDateLocator();axis.xaxis.set_major_locator(locator);axis.xaxis.set_major_formatter(ConciseDateFormatter(locator))
                    else:axis.scatter(xs,ys,c=ys,cmap='viridis',s=75,alpha=.85)
                    axis.set_xlabel(result['columns'][x][:160]+(' (UTC)' if plan['kind']=='line' else ''))
                    axis.set_ylabel(result['columns'][y][:160]);axis.set_title(plan['title'],loc='left',fontweight='bold')
                    axis.spines[['top','right']].set_visible(False);axis.grid(axis='y',alpha=.2)
                    if plan['kind']=='bar':axis.tick_params(axis='x',labelrotation=30)
                    output=BytesIO();figure.savefig(output,format='png',facecolor='#f6faf8')
                    image=base64.b64encode(output.getvalue()).decode('ascii')
                    if len(image)>262144:return needs_data('The static chart is too large. Use fewer points or shorter grouping labels.')
                finally:figure.clear()
        warnings=[*plan['warnings'],'Static chart: hover and zoom controls are not available.']
        if plan['kind']=='bar' and any(len(label)>40 for label in labels):warnings.append('Long axis labels are shortened; the table retains their complete text.')
        return {**plan,'figure':None,'kpi':None,'image':{'mime_type':'image/png','data':image},'warnings':warnings}


def result_identity(result):
    digest=hashlib.sha256();size=0
    encoder=json.JSONEncoder(default=encode,sort_keys=True,allow_nan=False)
    for chunk in encoder.iterencode(result):
        encoded=chunk.encode();size+=len(encoded)
        if size>262144:raise ValueError('Narrow the result below 256 KiB before visualization.')
        digest.update(encoded)
    return digest.hexdigest()


class VisualizationBusy(RuntimeError):pass


class VisualizationService:
    def __init__(self,max_calls=3,renderer=None,allowed_renderers=None):
        if not 1<=max_calls<=3:raise ValueError('VISUALIZATION_MAX_MODEL_CALLS must be 1–3')
        self.max_calls=max_calls;self.lock=threading.RLock();self.slots=threading.BoundedSemaphore(2)
        self.renderers={renderer.name:renderer} if renderer else {'plotly':PlotlyRenderer()}
        if renderer is None:
            for name in ('matplotlib','seaborn'):
                if importlib.util.find_spec(name):self.renderers[name]=StaticRenderer(name)
        if allowed_renderers is not None:
            if not allowed_renderers or set(allowed_renderers)-{'plotly','matplotlib','seaborn'}:raise ValueError('VISUALIZATION_RENDERERS supports plotly, matplotlib and seaborn')
            self.renderers={name:engine for name,engine in self.renderers.items() if name in allowed_renderers}
        self.cache={};self.inflight=set()

    def maintenance(self):
        with self.lock:self.cache={key:value for key,value in self.cache.items() if value[0]>time.time()}

    def erase(self,query_ids):
        identifiers=set(query_ids)
        with self.lock:
            self.cache={identity:value for identity,value in self.cache.items() if not (isinstance(identity[0],tuple) and identity[0][0] in identifiers)}

    def plan(self,key,result,create_model,guard,expires,presentation='auto'):
        guard()
        if result.get('rows_retained') is False:return needs_data('Result rows were not retained. Run the question again and approve its SQL to obtain fresh chart data.',['Run the question again with normal table confirmation and SQL approval.'])
        if presentation not in {'auto','interactive','static'}:raise ValueError('Unsupported presentation preference')
        identity=(key,result_identity(result),presentation)
        result,profile=profile_result(result)
        with self.lock:
            self.cache={k:v for k,v in self.cache.items() if v[0]>time.time()}
            if identity in self.cache:
                guard()
                return deepcopy(self.cache[identity][1])
            if identity in self.inflight or not self.slots.acquire(blocking=False):raise VisualizationBusy('Visualization capacity is busy. Retry shortly.')
            self.inflight.add(identity)
        model=None
        try:
            model=create_model();budget=Budget(self.max_calls)
            available=[{'library':name,'kinds':list(renderer.kinds),'interactive':renderer.interactive}
                for name,renderer in self.renderers.items() if presentation=='auto' or renderer.interactive==(presentation=='interactive')]
            if not available:raise ValueError('No configured renderer supports the requested presentation')
            raw=VisualizationAgent(model,budget).recommend(profile,available,presentation)
            validated=validate_plan(raw,result,profile)
            if validated['status']=='chart':
                renderer=self.renderers.get(validated['library'])
                if not renderer or validated['kind'] not in renderer.kinds or not any(item['library']==renderer.name for item in available):raise ValueError('The agent selected an unavailable renderer or incompatible presentation')
                proposal=renderer.render(validated,result)
                proposal['renderer']=renderer.name;proposal['interactive']=renderer.interactive and validated['kind']!='kpi'
            else:proposal={**validated,'figure':None,'kpi':None,'renderer':'none','interactive':False}
            proposal['model_calls']=budget.calls
            guard()
            with self.lock:
                guard()
                if len(self.cache)>=50:self.cache.pop(next(iter(self.cache)))
                self.cache[identity]=(min(expires,time.time()+600),deepcopy(proposal))
            return proposal
        finally:
            try:
                close=getattr(model,'close',None)
                if callable(close):close()
            finally:
                with self.lock:self.inflight.discard(identity)
                self.slots.release()

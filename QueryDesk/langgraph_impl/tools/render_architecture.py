"""Current QueryDesk architecture; matching scalable SVG and PNG exports."""
from pathlib import Path
from html import escape
import math
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[1]/"docs"/"architecture"
ROOT.mkdir(parents=True,exist_ok=True)
W,H=2600,2330
im=Image.new('RGB',(W,H),'#f5f7fb');d=ImageDraw.Draw(im)
svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="Current QueryDesk architecture and request flow">',f'<rect width="{W}" height="{H}" fill="#f5f7fb"/>']
NAVY='#153452';BLUE='#2768a7';MUTED='#5b6d80';GREEN='#16846d';ORANGE='#a6632b';LINE='#718ca8'
def font(size,bold=False):return ImageFont.truetype('C:/Windows/Fonts/segoeui'+('b' if bold else '')+'.ttf',size)
def text(x,y,value,size=25,color=NAVY,bold=False):
    d.text((x,y),value,font=font(size,bold),fill=color,anchor='lt')
    svg.append(f'<text x="{x}" y="{y}" dominant-baseline="text-before-edge" font-family="Segoe UI, Arial, sans-serif" font-size="{size}" font-weight="{700 if bold else 400}" fill="{color}">{escape(value)}</text>')
def rect(x,y,w,h,fill='#fff',stroke='#d7e0eb',radius=18,width=2):
    d.rounded_rectangle((x,y,x+w,y+h),radius=radius,fill=fill,outline=stroke,width=width)
    svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>')
def arrow(points,color=LINE,width=4,dashed=False,head=True):
    if dashed:
        for (x1,y1),(x2,y2) in zip(points,points[1:]):
            length=math.hypot(x2-x1,y2-y1)
            if not length:continue
            for a in range(0,math.ceil(length),22):
                b=min(a+13,length)
                d.line([(x1+(x2-x1)*a/length,y1+(y2-y1)*a/length),(x1+(x2-x1)*b/length,y1+(y2-y1)*b/length)],fill=color,width=width)
    else:d.line(points,fill=color,width=width,joint='curve')
    svg.append('<polyline points="'+' '.join(f'{x},{y}' for x,y in points)+f'" fill="none" stroke="{color}" stroke-width="{width}"'+(' stroke-dasharray="13 9"' if dashed else '')+'/>')
    if head:
        x,y=points[-1];a,b=points[-2];angle=math.atan2(y-b,x-a)
        poly=[(x,y),(x-14*math.cos(angle-.5),y-14*math.sin(angle-.5)),(x-14*math.cos(angle+.5),y-14*math.sin(angle+.5))]
        d.polygon(poly,fill=color)
        svg.append('<polygon points="'+' '.join(f'{x:.2f},{y:.2f}' for x,y in poly)+f'" fill="{color}"/>')
def card(x,y,w,h,title,lines,number=None,kind='normal',tag=None):
    color=GREEN if kind=='approval' else ORANGE if kind=='optional' else BLUE
    fill='#edf8f4' if kind=='approval' else '#fff9f1' if kind=='optional' else '#fff'
    rect(x,y,w,h,fill,color)
    title_x=x+24
    if number is not None:
        rect(x+20,y+20,44,36,color,color,9)
        text(x+27,y+23,str(number).zfill(2),23,'#fff',True);title_x=x+78
    size=29
    while font(size,True).getlength(title)>w-(title_x-x)-22:size-=1
    text(title_x,y+22,title,size,color,True)
    cursor=y+76
    for value in lines:
        fs=25
        while font(fs).getlength(value)>w-48:fs-=1
        assert fs>=19,(title,value,fs)
        text(x+24,cursor,value,fs,MUTED);cursor+=35
    if tag:text(x+24,y+h-35,tag,19,color,True)

# Header and key.
rect(80,60,66,66,BLUE,BLUE,17);text(97,67,'Q',46,'#fff',True)
text(170,57,'QueryDesk',60,NAVY,True)
text(80,152,'CURRENT ARCHITECTURE  /  ENGLISH QUESTION TO GOVERNED DATABASE ANSWER',28,MUTED,True)
text(1825,75,'Existing implementation only',26,BLUE,True)
text(1825,116,'Includes published domain knowledge and existing guardrails',23,MUTED)

# Application request entry.
top=[80,720,1360,2000]
for x in top[:-1]:arrow([(x+520,345),(x+640,345)],BLUE)
card(80,270,520,145,'Browser interface',['React / TypeScript','English question, source and domain'])
card(720,270,520,145,'Node.js frontend server',['Serves the built UI and proxies API calls','Same-origin browser requests'])
card(1360,270,520,145,'FastAPI + access controls',['Authentication, CSRF and input validation','Source, domain, table, row and PII scope'])
card(2000,270,520,145,'Query job manager',['Bounded queue, ownership and idempotency','Progress, cancellation and review recovery'])

# Main workflow canvas; the stages return to the orchestrator between steps.
rect(55,455,2490,735,'#edf2f8','#d7e0eb',26)
rect(80,480,2440,88,NAVY,NAVY,16)
text(110,497,'LANGGRAPH ORCHESTRATOR',32,'#fff',True)
text(755,503,'Controls stage transitions, bounded retries, model-call budgets and review pauses',27,'#dfeaf5')
arrow([(2260,415),(2260,480)],BLUE)
arrow([(280,568),(280,660)],BLUE)

xs=[80,590,1100,1610,2120];y1=660;y2=975;cw=400;ch=168
for x in xs[:-1]:arrow([(x+cw,y1+84),(x+510,y1+84)],BLUE)
arrow([(2320,y1+ch),(2320,y2)],BLUE)
for x in xs[1:]:arrow([(x,y2+84),(x-110,y2+84)],BLUE)

# Generation failures restart schema planning; edited SQL is revalidated.
arrow([(2520,1059),(2570,1059),(2570,611),(790,611),(790,660)],ORANGE,3,True)
rect(1060,585,835,45,'#edf2f8','#edf2f8',8)
text(1080,592,'Failed generated SQL: bounded schema / generation retry',24,ORANGE,True)
arrow([(1810,975),(1810,902),(2320,902),(2320,975)],GREEN,3,True)
text(1843,862,'User SQL edits return to validation',23,GREEN,True)

card(xs[0],y1,cw,ch,'Intent Agent',['Interpret the question','Clarify or refuse when needed'],1)
card(xs[1],y1,cw,ch,'Routing + Schema Agent',['Domain hints and permitted tables','Retrieve live schema and joins'],2)
card(xs[2],y1,cw,ch,'Confirm tables',['User selects permitted tables','No business-data reads yet'],3,'approval')
card(xs[3],y1,cw,ch,'Column Pruning Agent',['Retrieve definitions / SQL examples','Retain needed columns and keys'],4)
card(xs[4],y1,cw,ch,'SQL Agent',['Generate dialect-specific SQL','One read-only query + parameters'],5)
card(xs[4],y2,cw,ch,'Validation Agent',['Application planning checks','Model review of query semantics'],6)
card(xs[3],y2,cw,ch,'Approve final SQL',['User reviews SQL and access scope','Fingerprint, expiry and edit controls'],7,'approval')
card(xs[2],y2,cw,ch,'Execution Agent',['Consume current approval','Run through protected adapters'],8)
card(xs[1],y2,cw,ch,'Response Agent',['Explain the executed results','State units, filters and limitations'],9)
card(xs[0],y2,cw,ch,'Browser results',['Answer + data-based explanation','SQL, tables and tabular results'],10)
text(350,1157,'Arrows show request-stage order. Each agent stage returns control to the orchestrator.',22,MUTED)

# Optional visualization happens after a completed result, with no SQL rerun.
arrow([(280,1143),(280,1330)],ORANGE,3,True)
text(330,1245,'WHEN THE USER CHOOSES “VISUALIZE”',22,ORANGE,True)
card(80,1330,400,170,'Visualization Agent',['Recommend chart, axes and library','Typed plan; column profiles only'],11,'optional')
arrow([(480,1415),(590,1415)],ORANGE)
card(590,1330,400,170,'Chart in the browser',['Plotly interactive charts','Matplotlib / Seaborn static charts'],12,'optional')
rect(80,1550,910,195,'#fff','#d7e0eb',18)
text(108,1571,'Human control stays in the request flow',30,NAVY,True)
text(108,1622,'Approval pauses release workflow workers; models cannot approve SQL.',24,MUTED)
text(108,1661,'Clarification, refusal, cancellation or limits return a status to the user.',24,MUTED)
text(108,1700,'Visualization uses authorized results and does not execute new SQL.',24,MUTED)

# Existing supporting components, separate from the agent request sequence.
text(1100,1237,'SHARED SERVICES AND ENFORCED BOUNDARIES',26,NAVY,True)
card(1100,1290,680,180,'Business knowledge',['Built-in JSON + published, versioned domain definitions','Concepts, metrics, rules, relationships and SQL examples','Lexical / BM25 retrieval; current-access mapping checks'])
card(1840,1290,680,180,'Approved LLM providers',['Per-agent model configuration through .env','LangChain typed tool calls and local handoff validation','Provider policy checks; credentials stay server-side'])
card(1100,1530,680,225,'Connectors + databases / warehouses',['SQLite, PostgreSQL, MySQL, SQL Server and Oracle','Snowflake, Redshift and BigQuery','Mapped SQL Server joins across one server instance','Protected reads, PII/row scope and configured TLS'])
card(1840,1530,680,225,'Application state + operations',['SQLite accounts, sessions, query and knowledge records','History retention, feedback and safe review checkpoints','Owner checks and access-policy revocation','Stage latency, provider usage and execution metrics'])

# Explicit warehouse I/O path follows the protected execution boundary.
arrow([(1300,1143),(1300,1205),(1040,1205),(1040,1644),(1100,1644)],BLUE,3)
text(1068,1493,'Native SQL and scoped results',21,BLUE,True)

text(80,1810,'DOMAIN KNOWLEDGE AUTHORING  /  CONTROLLED PUBLICATION',28,NAVY,True)
for x in [80,590,1100,1610]:arrow([(x+400,1954),(x+510,1954)],GREEN)
card(80,1870,400,180,'Assign maintainers',['Administrator assigns domain roles','Data access is separately granted'])
card(590,1870,400,180,'Build a draft',['Owner / contributor supplies meaning','Forms, JSON or AI-assisted drafts'])
card(1100,1870,400,180,'Validate and submit',['Current permitted metadata only','No execution of example SQL'])
card(1610,1870,400,180,'Review and publish',['Reviewer confirms business meaning','Immutable revisions and rollback'],kind='approval')
card(2120,1870,400,180,'Published definitions',['Only the active published revision','Recheck each user’s current scope'])
arrow([(2520,1954),(2570,1954),(2570,1500),(1805,1500),(1805,1380),(1780,1380)],GREEN,3,True)
text(1845,1476,'Published context only',21,GREEN,True)
text(80,2090,'Drafts never enter generation context. Published knowledge supports intent, schema selection, column pruning, SQL and explanation.',24,MUTED)
text(80,2132,'Publishing, retirement or rollback invalidates old query approvals. Metadata validation does not prove business-result correctness.',24,MUTED)
arrow([(80,2200),(145,2200)],BLUE,4,False,False);text(160,2185,'Request flow',24,MUTED)
arrow([(440,2200),(505,2200)],ORANGE,3,True,False);text(520,2185,'Optional / retry path',24,MUTED)
rect(905,2185,35,29,'#edf8f4',GREEN,7);text(957,2185,'Human approval / published knowledge',24,MUTED)
text(80,2260,'Security is enforced by application code and database grants. Models cannot approve SQL or change data permissions.',24,NAVY)

svg.append('</svg>')
(ROOT/'querydesk-current.svg').write_text('\n'.join(svg),encoding='utf-8')
im.save(ROOT/'querydesk-current.png',optimize=True)
print(ROOT/'querydesk-current.png')

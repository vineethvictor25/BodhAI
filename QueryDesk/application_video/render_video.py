"""Reproducible motion graphics + actual browser recording, rendered locally."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'.tools'))
import json,math,subprocess,wave,textwrap
from PIL import Image,ImageDraw,ImageFont
import imageio_ffmpeg

W,H,FPS=1920,1080,24
BG='#f6f7f1';DARK='#183d30';GREEN='#326d50';LINE='#d9e2d4';MUTED='#657567';PALE='#e8f0e0';AMBER='#e3be6c'
FFMPEG=imageio_ffmpeg.get_ffmpeg_exe()
PLAN=json.loads((ROOT/'plan.json').read_text(encoding='utf-8'))
DUR=json.loads((ROOT/'audio/durations.json').read_text(encoding='utf-8'))
OUT=ROOT/'render';OUT.mkdir(exist_ok=True)
FONTROOT=Path('C:/Windows/Fonts')
def font(size,bold=False):return ImageFont.truetype(str(FONTROOT/('seguisb.ttf' if bold else 'segoeui.ttf')),size)
FONTS={(size,bold):font(size,bold) for size in (18,20,22,24,26,28,30,32,34,38,42,46,52,62,68) for bold in (False,True)}
def txt(draw,xy,text,size=28,color=DARK,bold=False):draw.text(xy,text,font=FONTS[(size,bold)],fill=color)
def wrap(draw,text,width,size=28,bold=False):
    result=[];line=''
    for word in text.split():
        if line and draw.textlength(line+' '+word,font=FONTS[(size,bold)])>width:result.append(line);line=word
        else:line=(line+' '+word).strip()
    if line:result.append(line)
    return result
def lines(draw,xy,text,width,size=28,color=DARK,bold=False,gap=10):
    x,y=xy
    for line in wrap(draw,text,width,size,bold):txt(draw,(x,y),line,size,color,bold);y+=size+gap
    return y
def box(draw,rect,fill='white',outline=LINE,width=2,radius=24):draw.rounded_rectangle(rect,radius=radius,fill=fill,outline=outline,width=width)
def arrow(draw,start,end,fill=GREEN,width=4):
    draw.line((start,end),fill=fill,width=width)
    angle=math.atan2(end[1]-start[1],end[0]-start[0]);length=14
    draw.polygon([end,(end[0]-length*math.cos(angle-.45),end[1]-length*math.sin(angle-.45)),(end[0]-length*math.cos(angle+.45),end[1]-length*math.sin(angle+.45))],fill=fill)
def chrome(scene,index):
    image=Image.new('RGB',(W,H),BG);draw=ImageDraw.Draw(image)
    draw.ellipse((1430,-300,2350,620),outline='#e4ebdd',width=2)
    box(draw,(75,43,113,81),DARK,DARK,radius=10)
    for i,height in enumerate((12,21,28)):draw.rounded_rectangle((84+i*8,73-height,89+i*8,73),radius=2,fill='#dcecc6')
    txt(draw,(130,38),'QueryDesk',34,DARK,True);txt(draw,(1560,48),'BUSINESS WALKTHROUGH',18,MUTED)
    txt(draw,(100,137),scene['eyebrow'],22,GREEN,True)
    lines(draw,(95,186),scene['title'],1720,62,DARK,True,8)
    draw.line((100,1010,1820,1010),fill=LINE,width=2)
    txt(draw,(100,1030),'English question  /  approved SQL  /  explainable results',20,MUTED)
    txt(draw,(1745,1030),f'{index:02d} / 08',20,MUTED)
    return image

def scene_base(scene,index):
    image=chrome(scene,index);d=ImageDraw.Draw(image);kind=scene['id'];animation={}
    if kind=='problem':
        box(d,(100,360,940,680),PALE,LINE)
        txt(d,(140,393),'THE QUESTION',20,GREEN,True)
        lines(d,(140,445),'Which customer groups have outstanding balances?',750,46,DARK,True,10)
        labels=[('01','Find the right tables'),('02','Agree on the metric'),('03','Join without double counting'),('04','Explain the number')]
        rects=[]
        for i,(number,label) in enumerate(labels):
            y=345+i*123;rect=(1070,y,1815,y+100);rects.append(rect);box(d,rect)
            txt(d,(1098,y+29),number,28,GREEN,True);txt(d,(1160,y+29),label,28)
        lines(d,(135,758),'Waiting for SQL should not be the normal route to a business answer.',1500,34,MUTED,False,12)
        animation={'rects':rects,'kind':'rect'}
    elif kind=='solution':
        cards=[('01','Ask in English','Describe the business question in familiar terms.'),('02','Review before reading','Confirm tables, filters and SQL before execution.'),('03','See the evidence','Read the answer, result rows and executed query.')]
        rects=[]
        for i,(num,title,body) in enumerate(cards):
            x=100+i*580;rect=(x,390,x+550,810);rects.append(rect);box(d,rect)
            box(d,(x+32,425,x+108,501),PALE,PALE,radius=20);txt(d,(x+47,437),num,34,GREEN,True)
            lines(d,(x+32,540),title,475,38,DARK,True,8);lines(d,(x+32,653),body,470,28,MUTED,False,12)
        txt(d,(100,893),'A browser interface for business and BI users',32,GREEN,True)
        animation={'rects':rects,'kind':'rect'}
    elif kind=='flow':
        labels=[('Source + domain','Choose the scope'),('English question','Describe the task'),('Confirm tables','Your decision'),('Approve SQL','Your decision'),('Read-only query','Database execution'),('Answer + rows','Inspect the evidence')]
        rects=[]
        for i,(title,body) in enumerate(labels):
            x=95+i*292;rect=(x,430,x+267,720);rects.append(rect);box(d,rect,('#fff8e8' if i in {2,3} else 'white'))
            txt(d,(x+24,456),f'{i+1:02d}',30,GREEN,True);lines(d,(x+24,525),title,220,30,DARK,True,5);lines(d,(x+24,635),body,224,22,MUTED,False,5)
            if i<5:arrow(d,(x+270,570),(x+288,570))
        box(d,(100,805,1815,929),PALE,LINE)
        txt(d,(135,835),'No business-data read before final SQL approval',34,GREEN,True)
        txt(d,(135,884),'Each question is an independent query; this is not an open-ended chatbot.',24,MUTED)
        animation={'rects':rects,'kind':'rect'}
    elif kind=='architecture':
        roles=[('Intent','Understand the question'),('Schema + columns','Retrieve useful metadata'),('SQL','Generate the candidate'),('Validation','Check structure and semantics'),('Execution','Run approved reads'),('Response','Explain returned results')]
        rects=[];centers=[]
        for i,(title,body) in enumerate(roles):
            x=100+(i%3)*580;y=350 if i<3 else 750;centers.append((x+265,y+70));rects.append((x,y,x+530,y+140))
        for center in centers:arrow(d,(960,620),center,LINE,4)
        for i,(title,body) in enumerate(roles):
            x,y=rects[i][:2];box(d,rects[i]);txt(d,(x+28,y+23),title,34,DARK,True);txt(d,(x+28,y+81),body,24,MUTED)
        box(d,(715,540,1205,680),DARK,DARK)
        txt(d,(768,564),'LangGraph orchestrator',34,'white',True);txt(d,(799,622),'Coordinates the workflow',24,'#d6e8cb')
        box(d,(100,938,1815,990),PALE,PALE,radius=16)
        txt(d,(135,944),'Shared context: schema  •  business definitions  •  reviewed SQL examples',26,GREEN)
        animation={'rects':rects,'centers':centers,'kind':'graph'}
    elif kind=='controls':
        items=[('Identity + permissions','Users, sources and assigned domains'),('Configured row / PII rules','Scope and protect sensitive data'),('Human approval','Inspect SQL before the database read'),('Read and result limits','Bound query output and local PII work'),('Verified TLS by default','Development opt-out where supported'),('Business validation','Review definitions and known answers')]
        rects=[]
        for i,(title,body) in enumerate(items):
            x=100+(i%2)*875;y=350+(i//2)*175;rect=(x,y,x+840,y+150);rects.append(rect);box(d,rect)
            txt(d,(x+26,y+22),title,32,DARK,True);txt(d,(x+26,y+84),body,24,MUTED)
        txt(d,(105,930),'Controls improve reviewability. They do not prove business correctness.',26,GREEN,True)
        animation={'rects':rects,'kind':'rect'}
    elif kind=='value':
        labels=[('Answer','What the rows say'),('Reason','Filters, units and calculation'),('Result table','Actual returned values'),('Executed SQL','A reviewable query record')]
        rects=[]
        for i,(title,body) in enumerate(labels):
            x=100+i*438;rect=(x,365,x+412,540);rects.append(rect);box(d,rect);txt(d,(x+25,391),title,30,DARK,True);lines(d,(x+25,454),body,355,24,MUTED)
        x=100;y=610;box(d,(x,y,1815,904),'white',LINE)
        cols=[('Segment',140),('Region',550),('Orders',900),('Net cash (INR)',1160),('Outstanding (INR)',1465)]
        for title,cx in cols:txt(d,(cx,y+22),title,26,GREEN,True)
        rows=[('New','West','1','680','680'),('Retail','South','2','270','500'),('VIP','North','5','920','450')]
        for j,row in enumerate(rows):
            ry=y+88+j*63;d.line((125,ry-8,1790,ry-8),fill=LINE,width=1)
            for value,(_,cx) in zip(row,cols):txt(d,(cx,ry),value,28)
        txt(d,(105,936),'Unrestricted sample reference preview • full report: 10 groups / 22 orders',24,MUTED)
        animation={'rects':rects,'kind':'rect'}
    elif kind=='close':
        for i,(title,body) in enumerate([('01  Pick one domain','Start with a small, meaningful business scope.'),('02  Define the measures','Agree on filters, units and relationships.'),('03  Prove the answers','Evaluate known questions with your data owners.')]):
            y=350+i*175;box(d,(100,y,1815,y+145));txt(d,(135,y+23),title,34,DARK,True);txt(d,(135,y+83),body,28,MUTED)
        txt(d,(105,920),'Connect  •  Govern  •  Ask  •  Review',38,GREEN,True)
        animation={'rects':[(100,350+i*175,1815,495+i*175) for i in range(3)],'kind':'rect'}
    return image,animation

def animate(base,animation,t,duration):
    frame=base.copy();d=ImageDraw.Draw(frame)
    rects=animation.get('rects',[])
    if rects:
        index=min(len(rects)-1,int((t/max(duration,1))*len(rects)))
        rect=rects[index];d.rounded_rectangle(rect,radius=24,outline=GREEN,width=5)
        if animation['kind']=='graph':
            end=animation['centers'][index];phase=(t*0.7)%1
            x=960+(end[0]-960)*phase;y=620+(end[1]-620)*phase
            d.ellipse((x-10,y-10,x+10,y+10),fill=GREEN)
    d.rounded_rectangle((100,1006,100+int(1720*min(1,t/duration)),1014),radius=4,fill=GREEN)
    fade=min(1,t/.4,(duration-t)/.4)
    if fade<1:frame=Image.blend(Image.new('RGB',(W,H),BG),frame,max(0,fade))
    return frame

def run(args):
    result=subprocess.run([FFMPEG,'-hide_banner','-loglevel','error','-y',*args],capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise RuntimeError(result.stderr.decode(errors='replace')[-4000:])

ENC=['-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p','-threads','4','-c:a','aac','-ar','48000','-ac','2','-b:a','128k','-movflags','+faststart']
def render_slide(scene,index):
    duration=DUR[scene['id']]+.8;target=OUT/(scene['id']+'.mp4')
    if target.exists() and target.stat().st_size>10000:
        print('Reusing rendered '+scene['id'],flush=True)
        return {'file':target,'duration':math.ceil(duration*FPS)/FPS,'scene':scene}
    base,animation=scene_base(scene,index);base.save(OUT/(scene['id']+'.png'))
    cmd=[FFMPEG,'-hide_banner','-loglevel','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','pipe:0','-i',str(ROOT/'audio'/(scene['id']+'.wav')),'-af','apad','-t',str(duration),*ENC,str(target)]
    error=open(OUT/(scene['id']+'.log'),'wb')
    process=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=error,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        for n in range(math.ceil(duration*FPS)):process.stdin.write(animate(base,animation,n/FPS,duration).tobytes())
        process.stdin.close();code=process.wait()
    finally:error.close()
    if code:raise RuntimeError('Slide encoding failed: '+scene['id'])
    print('Rendered '+scene['id'],flush=True)
    return {'file':target,'duration':math.ceil(duration*FPS)/FPS,'scene':scene}

def wav_demo(timeline,duration):
    with wave.open(str(ROOT/'audio/demo_ask.wav'),'rb') as source:
        params=source.getparams();rate=source.getframerate();sample_bytes=source.getnchannels()*source.getsampwidth()
    data=bytearray(math.ceil(duration*rate)*sample_bytes)
    for mark in timeline['marks']:
        with wave.open(str(ROOT/'audio'/(mark['id']+'.wav')),'rb') as source:clip=source.readframes(source.getnframes())
        at=int(mark['seconds']*rate)*sample_bytes;data[at:at+len(clip)]=clip
    with wave.open(str(ROOT/'audio/demo.wav'),'wb') as target:target.setparams(params);target.writeframes(data)

def demo_frame():
    frame=Image.new('RGB',(W,H),BG);d=ImageDraw.Draw(frame)
    txt(d,(65,22),'QueryDesk  /  Application demo',34,DARK,True)
    txt(d,(955,32),'Scripted agent responses  •  Real graph + SQLite',24,GREEN)
    box(d,(156,86,1764,994),'white',LINE,radius=10)
    txt(d,(160,1021),'Ask in English  →  Confirm tables  →  Approve SQL  →  Inspect results',24,DARK,True)
    txt(d,(1585,1021),'06 / 08',22,MUTED)
    frame.save(OUT/'demo_frame.png')
    return frame

def render_demo():
    capture=ROOT/'capture/demo.webm'
    count,secs=imageio_ffmpeg.count_frames_and_secs(str(capture));timeline=json.loads((ROOT/'capture/timeline.json').read_text())
    duration=max(secs,timeline['total_seconds']);wav_demo(timeline,duration);demo_frame()
    target=OUT/'demo.mp4'
    run(['-loop','1','-framerate',str(FPS),'-i',str(OUT/'demo_frame.png'),'-i',str(capture),'-i',str(ROOT/'audio/demo.wav'),'-filter_complex','[1:v]fps=24,setpts=PTS-STARTPTS[app];[0:v][app]overlay=160:90:shortest=1[v]','-map','[v]','-map','2:a','-t',str(duration),*ENC,str(target)])
    print('Rendered actual application demo',flush=True)
    return {'file':target,'duration':duration,'timeline':timeline}

def timestamp(seconds,srt=False):
    ms=int(round(seconds*1000));h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000)
    return f'{h:02}:{m:02}:{s:02}'+(',' if srt else '.')+f'{ms:03}'

def captions_for(scene,start):
    words=scene['narration'].split();chunks=[' '.join(words[i:i+13]) for i in range(0,len(words),13)]
    duration=DUR[scene['id']];result=[];offset=0
    for chunk in chunks:
        seconds=duration*len(chunk.split())/len(words)
        result.append((start+offset,start+offset+seconds,'\n'.join(textwrap.wrap(chunk,58))))
        offset+=seconds
    return result

def main():
    segments=[];story=[];captions=[];chapter_text=[];now=0
    for index,scene in enumerate([p for p in PLAN if not p['id'].startswith('demo_')],1):
        if scene['id']=='value':
            demo=render_demo();segments.append(demo);chapter_text.append((now,'Application demo'))
            for mark in demo['timeline']['marks']:
                demo_scene=next(p for p in PLAN if p['id']==mark['id']);captions.extend(captions_for(demo_scene,now+mark['seconds']))
            now+=demo['duration']
            shot=Image.open(ROOT/'capture/06_results.png').convert('RGB');story.append(('Application demo',shot))
        actual_index=index if index<=5 else index+1
        segment=render_slide(scene,actual_index);segments.append(segment);chapter_text.append((now,scene['title']));captions.extend(captions_for(scene,now));now+=segment['duration']
        story.append((scene['title'],Image.open(OUT/(scene['id']+'.png')).convert('RGB')))
    (OUT/'concat.txt').write_text(''.join("file '"+str(s['file']).replace('\\','/')+"'\n" for s in segments),encoding='utf-8')
    final=ROOT/'QueryDesk_business_walkthrough.mp4'
    run(['-f','concat','-safe','0','-i',str(OUT/'concat.txt'),'-c:v','copy','-af','loudnorm=I=-16:TP=-1.5:LRA=11','-c:a','aac','-b:a','160k','-ar','48000','-movflags','+faststart',str(final)])
    (ROOT/'QueryDesk_captions.srt').write_text('\n\n'.join(f'{i+1}\n{timestamp(a,True)} --> {timestamp(b,True)}\n{text}' for i,(a,b,text) in enumerate(captions))+'\n',encoding='utf-8')
    (ROOT/'chapters.txt').write_text('\n'.join(timestamp(t).split('.')[0]+' '+name for t,name in chapter_text)+'\n',encoding='utf-8')
    transcript='# QueryDesk business walkthrough\n\nAudience: business and BI users. English synthetic narration: Microsoft Zira Desktop.\n\nDemo disclosure: scripted model responses; real LangGraph workflow, protected read-only SQLite execution, authentication and browser UI. No live model provider or remote warehouse connection is represented.\n\n'
    for scene in PLAN:transcript+='## '+scene['title']+'\n\n'+scene['narration']+'\n\n'
    (ROOT/'narration_script.md').write_text(transcript,encoding='utf-8')
    sheet=Image.new('RGB',(1280,2*230),BG);d=ImageDraw.Draw(sheet)
    for i,(name,image) in enumerate(story):
        x=(i%4)*320;y=(i//4)*230;image.thumbnail((312,176));sheet.paste(image,(x+4,y+4));lines(d,(x+8,y+183),name,300,18,DARK,False,3)
    sheet.save(ROOT/'storyboard.png')
    poster=scene_base(PLAN[1],2)[0];poster.save(ROOT/'poster.png')
    metadata={'width':W,'height':H,'fps':FPS,'estimated_duration_seconds':now,'video':final.name,'demo':'Scripted agent responses, real LangGraph + SQLite + UI','chapters':[{'seconds':t,'title':name} for t,name in chapter_text]}
    (ROOT/'video_metadata.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    print('FINAL: '+str(final)+' ('+str(round(now,1))+' seconds)',flush=True)

if __name__=='__main__':main()

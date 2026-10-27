"""Original, silent UI animatics for the public-profile lab. No personal media."""
from pathlib import Path
import math
import subprocess
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1] / 'public' / 'candidate-public-profile'
ROOT.mkdir(parents=True, exist_ok=True)
W, H, FPS = 1280, 720, 24
FONT = '/System/Library/Fonts/Supplemental/Arial.ttf'
BOLD = '/System/Library/Fonts/Supplemental/Arial Bold.ttf'
def font(size, bold=False): return ImageFont.truetype(BOLD if bold else FONT, size)
def txt(draw, xy, value, size=30, fill='#24212c', bold=False): draw.text(xy, value, font=font(size,bold), fill=fill)
def box(draw, xy, color, r=24): draw.rounded_rectangle(xy, radius=r, fill=color)
def ease(t): return 1-(1-max(0,min(1,t)))**3

def intro(t):
    im=Image.new('RGB',(W,H),'#f1effa'); d=ImageDraw.Draw(im)
    scene=min(3,int(t/4.5)); phase=(t-scene*4.5)/4.5; shift=int(35*(1-ease(phase*4)))
    txt(d,(64,48),'МашаДаша',26,bold=True)
    txt(d,(1140,52),f'0{scene+1}',20,fill='#817994')
    titles=['Одно интервью.','За резюме — человек.','Каждый вывод — с контекстом.','Продолжите знакомство.']
    txt(d,(64,118+shift),titles[scene],49,bold=True)
    if scene==0:
        txt(d,(64,188+shift),'Больше, чем строчка об опыте.',25,fill='#777080')
        box(d,(285,278,995,625),'#ffffff',34)
        box(d,(309,302,680,601),'#26212e',24)
        d.ellipse((430,351,559,480),fill='#9f92d7'); txt(d,(454,390),'АМ',48,fill='#ffffff',bold=True)
        for i,width in enumerate([235,198,220]): box(d,(720,345+i*65,720+width,360+i*65),'#e5e0f2',7)
        for i in range(17):
            h=8+24*abs(math.sin(t*3+i*.6));box(d,(378+i*13,555-h,383+i*13,555+h),'#c5b7ef',2)
    elif scene==1:
        box(d,(64,265,1216,623),'#ffffff',34)
        txt(d,(103,305),'Анна Морозова',35,bold=True);txt(d,(103,357),'Customer Success Manager',24,fill='#777080')
        txt(d,(103,439),'«Сначала выясняю, какого результата',34)
        txt(d,(103,487),'хочет клиент. Потом предлагаю решение».',34)
        box(d,(1019,475,1150,532),'#f1edff',22);txt(d,(1046,490),'00:10',22,fill='#6752ca')
    elif scene==2:
        box(d,(64,260,1216,628),'#ffffff',34)
        for radius in [35,65,95,125]:d.ellipse((320-radius,443-radius,320+radius,443+radius),outline='#e7e0f2',width=2)
        d.line([(320,318),(320,568)],fill='#e7e0f2',width=2);d.line([(195,443),(445,443)],fill='#e7e0f2',width=2)
        d.polygon([(320,343),(412,443),(320,505),(244,443)],fill='#e5dcff',outline='#8c72df',width=3)
        txt(d,(582,340),'Навыки, которые видно',34,bold=True)
        txt(d,(582,399),'Разбор · расшифровка · фрагменты',24,fill='#777080')
        box(d,(582,483,1117,558),'#f4f2f8',20);txt(d,(612,508),'Смотрите момент, на котором основан вывод.',21)
    else:
        txt(d,(64,195),'Интервью, резюме и разбор уже рядом.',27,fill='#777080')
        box(d,(280,320,1000,589),'#ffffff',34)
        txt(d,(328,370),'Анна Морозова',38,bold=True);txt(d,(328,426),'Customer Success Manager',25,fill='#777080')
        box(d,(328,487,746,553),'#24212c',28);txt(d,(359,507),'Пригласить на встречу  ↗',26,fill='#ffffff')
    return im

def interview(t):
    im=Image.new('RGB',(W,H),'#19171f');d=ImageDraw.Draw(im)
    d.ellipse((355,100,925,670),fill='#24202f')
    d.ellipse((434,179,846,591),fill='#332d43')
    d.ellipse((504,249,776,521),fill='#716486');txt(d,(561,327),'АМ',94,fill='#f4eeff',bold=True)
    for i in range(31):
        height=8+20*abs(math.sin(t*3.5+i*.7)*math.sin(t+i*.2));box(d,(463+i*12,570-height,468+i*12,570+height),'#cdbfe7',3)
    txt(d,(47,40),'Демонстрационная запись',19,fill='#a59bb8')
    txt(d,(48,641),['Опыт в клиентском сервисе','Как выяснить задачу клиента','Работа с возражениями','Адаптация к новой команде'][min(3,int(t/8))],26,fill='#dcd3ed')
    return im

for name,seconds,render in [('masha-intro',18,intro),('sample-interview',32,interview)]:
    command=['ffmpeg','-y','-loglevel','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','-','-an','-c:v','libx264','-preset','fast','-crf','25','-pix_fmt','yuv420p','-movflags','+faststart',str(ROOT/f'{name}.mp4')]
    proc=subprocess.Popen(command,stdin=subprocess.PIPE)
    for frame in range(seconds*FPS):proc.stdin.write(render(frame/FPS).tobytes())
    proc.stdin.close()
    if proc.wait()!=0:raise RuntimeError(name)
    render(0.8).save(ROOT/f'{name}.jpg',quality=90)
    print(name, (ROOT/f'{name}.mp4').stat().st_size)

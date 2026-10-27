// Adapted from Pulse intake's MediaRecorder lifecycle. Audio lives in memory only.
export type VoiceState='idle'|'requesting'|'recording'|'transcribing';
type Options={upload:(b:Blob,s:AbortSignal)=>Promise<string>;onState:(s:VoiceState,n:number)=>void;onResult:(t:string)=>void;onError:(e:string)=>void};
export class VoiceInput {
  state:VoiceState='idle'; generation=0; stream:MediaStream|null=null; recorder:MediaRecorder|null=null;
  timer:ReturnType<typeof setInterval>|null=null;abort:AbortController|null=null;
  constructor(private options:Options){}
  setState(s:VoiceState,n=0){this.state=s;this.options.onState(s,n);}
  release(){if(this.timer)clearInterval(this.timer);this.timer=null;this.stream?.getTracks().forEach(t=>t.stop());this.stream=null;}
  async start(){
    if(this.state!=='idle')return;
    if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){this.options.onError('Запись не поддерживается. Откройте макет в Safari или Chrome.');return;}
    const generation=++this.generation;this.setState('requesting');
    try{
      const stream=await navigator.mediaDevices.getUserMedia({audio:true});
      if(generation!==this.generation){stream.getTracks().forEach(t=>t.stop());return;}
      this.stream=stream;
      const mime=['audio/webm;codecs=opus','audio/mp4','audio/ogg;codecs=opus'].find(t=>MediaRecorder.isTypeSupported(t));
      const recorder=new MediaRecorder(stream,{...(mime?{mimeType:mime}:{}),audioBitsPerSecond:32000});this.recorder=recorder;
      const chunks:Blob[]=[];let bytes=0;
      recorder.ondataavailable=e=>{if(e.data.size&&generation===this.generation){chunks.push(e.data);bytes+=e.data.size;if(bytes>5_000_000){this.cancel();this.options.onError('Ответ слишком длинный. Запишите его короче.');}}};
      recorder.onerror=()=>{if(generation===this.generation){this.cancel();this.options.onError('Запись прервалась. Попробуйте ещё раз.');}};
      recorder.onstop=async()=>{
        if(generation!==this.generation)return;this.release();
        const blob=new Blob(chunks,{type:recorder.mimeType||mime||'audio/webm'});
        if(blob.size<100){this.setState('idle');this.options.onError('Запись пустая. Попробуйте ещё раз.');return;}
        this.setState('transcribing');this.abort=new AbortController();const timer=setTimeout(()=>this.abort?.abort(),90000);
        try{const text=await this.options.upload(blob,this.abort.signal);if(generation===this.generation)this.options.onResult(text);}
        catch(e){if(generation===this.generation)this.options.onError(e instanceof Error?e.message:'Не удалось распознать запись.');}
        finally{clearTimeout(timer);if(generation===this.generation){this.abort=null;this.setState('idle');}}
      };
      recorder.start(500);const started=Date.now();this.setState('recording');
      this.timer=setInterval(()=>{const n=Math.floor((Date.now()-started)/1000);this.options.onState('recording',n);if(n>=120)this.stop();},250);
    }catch(e){this.release();if(generation!==this.generation)return;this.setState('idle');this.options.onError(e instanceof DOMException&&e.name==='NotAllowedError'?'Разрешите доступ к микрофону в браузере.':'Не удалось включить микрофон.');}
  }
  stop(){if(this.state!=='recording')return;this.setState('transcribing');if(this.recorder&&this.recorder.state!=='inactive')this.recorder.stop();this.release();}
  cancel(){++this.generation;this.abort?.abort();this.abort=null;if(this.recorder&&this.recorder.state!=='inactive')this.recorder.stop();this.release();this.setState('idle');}
}

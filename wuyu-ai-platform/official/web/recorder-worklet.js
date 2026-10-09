/* Capture mono PCM16 at 16 kHz in 40 ms frames, independent of device rate. */
class WuyuPCMRecorder extends AudioWorkletProcessor {
  constructor(){super();this.ratio=sampleRate/16000;this.phase=0;this.sum=0;this.count=0;this.frame=new Int16Array(640);this.used=0;this.ended=false;this.port.onmessage=e=>{if(e.data?.type==='flush'){this.flush();this.ended=true;this.port.postMessage({type:'flushed'});}};}
  flush(){if(!this.used)return;const buffer=this.frame.slice(0,this.used).buffer;this.port.postMessage({type:'pcm',buffer},[buffer]);this.used=0;}
  process(inputs){if(this.ended)return true;const channels=inputs[0];if(!channels?.length)return true;for(let i=0;i<channels[0].length;i++){let sample=0;for(const channel of channels)sample+=channel[i]||0;sample/=channels.length;this.sum+=sample;this.count++;this.phase++;if(this.phase>=this.ratio){const value=Math.max(-1,Math.min(1,this.sum/this.count));this.frame[this.used++]=Math.round(value<0?value*32768:value*32767);this.phase-=this.ratio;this.sum=0;this.count=0;if(this.used===640)this.flush();}}return true;}
}
registerProcessor('wuyu-pcm-recorder',WuyuPCMRecorder);

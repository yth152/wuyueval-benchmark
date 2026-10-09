import React,{useEffect,useRef,useState} from 'react';
import flow from './auth-flow.png';

// Animated texture coordinates deform the silk itself, instead of panning a still.
export default function FlowCanvas({paused}){
 const canvas=useRef(null),pause=useRef(paused),[ready,setReady]=useState(false);pause.current=paused;
 useEffect(()=>{
  const element=canvas.current,gl=element.getContext('webgl',{alpha:false,antialias:false,powerPreference:'low-power'});
  if(!gl)return;
  let frame=0,disposed=false,last=0,elapsed=0,loaded=false;const resources=[];
  const shader=(type,source)=>{const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);resources.push(['Shader',s]);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error('shader');return s;};
  try{
   const program=gl.createProgram();resources.push(['Program',program]);
   gl.attachShader(program,shader(gl.VERTEX_SHADER,'attribute vec2 position; varying vec2 uv; void main(){uv=position*.5+.5;gl_Position=vec4(position,0.,1.);}'));
   gl.attachShader(program,shader(gl.FRAGMENT_SHADER,`precision mediump float; varying vec2 uv; uniform sampler2D art; uniform vec2 cover; uniform float clock;
    void main(){ vec2 p=uv; float t=clock;
      vec2 drift=vec2(sin(p.y*5.8+t*.5)+.45*cos(p.x*7.-t*.3),cos(p.x*5.2-t*.38)+.4*sin(p.y*8.+t*.23));
      p+=drift*.039; p=(p-.5)*cover*.86+.5;
      vec3 color=texture2D(art,vec2(p.x,1.-p.y)).rgb;
      float light=sin(uv.x*5.+uv.y*3.-t*.55)*.027;
      gl_FragColor=vec4(clamp(color+light,0.,1.),1.);
    }`));
   gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error('program');gl.useProgram(program);
   const buffer=gl.createBuffer();resources.push(['Buffer',buffer]);gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),gl.STATIC_DRAW);
   const pos=gl.getAttribLocation(program,'position');gl.enableVertexAttribArray(pos);gl.vertexAttribPointer(pos,2,gl.FLOAT,false,0,0);
   const texture=gl.createTexture();resources.push(['Texture',texture]);gl.bindTexture(gl.TEXTURE_2D,texture);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
   const clock=gl.getUniformLocation(program,'clock'),cover=gl.getUniformLocation(program,'cover'),img=new Image();
   const draw=()=>{const rect=element.getBoundingClientRect(),ratio=Math.min(devicePixelRatio||1,1.4);const w=Math.max(1,Math.round(rect.width*ratio)),h=Math.max(1,Math.round(rect.height*ratio));if(element.width!==w||element.height!==h){element.width=w;element.height=h;gl.viewport(0,0,w,h);}const aspect=(w/h)/(img.width/img.height);gl.uniform2f(cover,Math.min(1,aspect),Math.min(1,1/aspect));gl.uniform1f(clock,elapsed);gl.drawArrays(gl.TRIANGLES,0,6);};
   const tick=now=>{if(disposed)return;if(loaded&&(!pause.current||!last)&&now-last>=32){if(!pause.current&&last)elapsed+=Math.min((now-last)/1000,.1);draw();last=now;}if(pause.current)last=now;frame=requestAnimationFrame(tick);};
   img.onload=()=>{if(disposed)return;gl.bindTexture(gl.TEXTURE_2D,texture);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGB,gl.RGB,gl.UNSIGNED_BYTE,img);loaded=true;draw();setReady(true);frame=requestAnimationFrame(tick);};img.src=flow;
  }catch{setReady(false);}
  const lost=()=>{cancelAnimationFrame(frame);setReady(false);};element.addEventListener('webglcontextlost',lost);
  return()=>{disposed=true;cancelAnimationFrame(frame);element.removeEventListener('webglcontextlost',lost);for(const [kind,value] of resources)gl['delete'+kind](value);};
 },[]);
 return <canvas ref={canvas} className="auth-flow-canvas" data-ready={ready} aria-hidden="true"/>;
}

"""Small dependency-free operator dashboard for recording sessions."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlparse


_HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Piper VLA Capture</title><style>
:root{color-scheme:dark;--bg:#111315;--line:#353a3f;--text:#f3f4f5;--muted:#aeb4ba;--ok:#43b581;--warn:#e2a93b;--bad:#e05252;--accent:#4da3ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 system-ui,sans-serif;letter-spacing:0}header{border-bottom:1px solid var(--line);padding:18px 24px;display:flex;align-items:center;justify-content:space-between;gap:16px}h1{font-size:20px;margin:0;overflow-wrap:anywhere}main{max-width:1160px;margin:auto;padding:24px;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px}.section{border-top:1px solid var(--line);padding-top:16px;margin-top:18px}h2{font-size:15px;margin:0 0 12px;color:var(--muted);font-weight:600}.status{font-size:34px;font-weight:700}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:12px 0}.checks{display:grid;gap:8px}.check{display:grid;grid-template-columns:88px minmax(0,1fr);gap:10px;padding:8px 0;border-bottom:1px solid #25292d}.PASS{color:var(--ok)}.WARN{color:var(--warn)}.BLOCKED{color:var(--bad)}input,select{width:100%;min-width:0;background:#0d0f11;border:1px solid var(--line);color:var(--text);padding:11px;border-radius:4px}label{display:block;color:var(--muted);margin:12px 0 5px;overflow-wrap:anywhere}button{border:0;border-radius:4px;padding:11px 14px;background:var(--accent);color:#07101a;font-weight:700;cursor:pointer}button.secondary{background:#3a4046;color:var(--text)}button.danger{background:var(--bad);color:white}button:disabled{opacity:.38;cursor:not-allowed}.metric{display:flex;justify-content:space-between;gap:16px;padding:7px 0;border-bottom:1px solid #25292d}.muted{color:var(--muted)}#message{min-height:22px;margin-top:12px}.signal{grid-column:1/-1;border-left:6px solid var(--accent);background:#181c20;padding:16px 18px;min-height:82px;overflow-wrap:anywhere}.signal strong{display:block;font-size:24px}.signal span{color:var(--muted)}.signal.recording,.signal.error{border-color:var(--bad);background:#281719}.signal.success{border-color:var(--ok);background:#15231d}.signal.failure,.signal.finalizing{border-color:var(--warn);background:#282216}.segmented{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:2px;background:#0d0f11;border:1px solid var(--line);padding:3px;border-radius:5px}.segmented button{min-width:0;background:transparent;color:var(--muted);white-space:normal;overflow-wrap:anywhere}.segmented button.selected{background:#3a4046;color:var(--text)}.form-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:0 12px}.wide{grid-column:1/-1}@media(max-width:760px){header{padding:16px}header .muted{display:none}main{grid-template-columns:minmax(0,1fr);padding:16px}.status{font-size:28px}.form-grid{grid-template-columns:minmax(0,1fr)}.wide{grid-column:auto}}
</style></head><body><header><h1>Piper VLA Capture</h1><div id="session" class="muted"></div></header><main>
<div id="signal" class="signal"><strong id="signalTitle">正在连接</strong><span id="signalDetail"></span></div>
<section><div class="section"><h2>录制状态</h2><div id="state" class="status">CONNECTING</div><div id="episode" class="muted"></div></div>
<div class="section"><h2>相机模式</h2><div class="segmented"><button id="cameraOff" onclick="setCameraMode('off')">CAMERAS OFF</button><button id="cameraMosaic" onclick="setCameraMode('mosaic')">3-CAMERA MOSAIC</button></div></div>
<div class="section"><h2>Episode</h2><div class="form-grid"><div><label>操作者</label><input id="operator" value="operator"></div><div><label>任务 ID</label><input id="task" value="bimanual_manipulation"></div><div class="wide"><label>Language instruction (English)</label><input id="instruction" value="Pick up the object and place it at the target location."></div></div><div class="row"><button id="start" onclick="startEpisode()">开始录制</button><button id="success" onclick="finishEpisode(true)">成功结束</button><button id="failed" class="secondary" onclick="finishEpisode(false)">失败结束</button><button id="abort" class="danger" onclick="abortEpisode()">中止</button></div><div id="message"></div></div>
<div class="section"><h2>VLA Language Action</h2><div class="form-grid"><div><label>Primitive</label><select id="primitive"></select></div><div><label>Arm</label><select id="arm"><option>left</option><option>right</option><option>bimanual</option></select></div><div><label>Object</label><input id="objectName" value="the object"></div><div><label>Target</label><input id="targetName" value="the target location"></div><div class="wide"><label>Language action (English)</label><input id="languageAction" value="Grasp the object with the right gripper."></div></div><div class="row"><button id="addAction" class="secondary" onclick="addLanguageAction()">添加动作标记</button></div></div></section>
<section><div class="section"><h2>系统预检</h2><div id="checks" class="checks"></div></div><div class="section"><h2>实时数据源</h2><div id="sources"></div></div></section>
</main><script>
async function request(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});const j=await r.json();if(!r.ok)throw new Error(j.error||'request failed');return j}
function setSignal(title,level,detail){signal.className='signal '+(level||'');signalTitle.textContent=title;signalDetail.textContent=detail||''}
async function startEpisode(){try{const r=await request('/api/start',{operator_id:operator.value,task_id:task.value,language_instruction:instruction.value});message.textContent='录制已开始';setSignal('正在录制','recording',r.episode_id)}catch(e){message.textContent=e.message;setSignal('无法开始录制','error',e.message)}}
async function finishEpisode(ok){try{setSignal(ok?'正在保存：成功标记':'正在保存：失败标记','finalizing','请勿关闭进程');const r=await request('/api/finish',{task_success:ok,failure_reason:ok?'none':'task_failed'});const valid=r.validation&&r.validation.valid;message.textContent=valid?'Episode 已保存并通过校验':'Episode 已保存但校验失败';setSignal(valid?(ok?'已保存：成功标记':'已保存：失败标记'):'保存完成，但数据校验失败',valid?(ok?'success':'failure'):'error',r.path||'')}catch(e){message.textContent=e.message;setSignal('保存失败','error',e.message)}}
async function abortEpisode(){try{const r=await request('/api/abort',{reason:'operator_abort'});message.textContent='Episode 已中止并保留';setSignal('已中止：不可用于训练','error',r.path||'')}catch(e){message.textContent=e.message;setSignal('中止失败','error',e.message)}}
async function setCameraMode(mode){try{message.textContent='正在切换相机模式';await request('/api/camera-mode',{mode});message.textContent=mode==='off'?'相机已完全关闭':'三相机预览正在启动'}catch(e){message.textContent=e.message}}
async function addLanguageAction(){try{const r=await request('/api/language-action',{primitive:primitive.value,arm:arm.value,object:objectName.value,target:targetName.value,language_action:languageAction.value});message.textContent=`已标记 ${r.primitive}: ${r.language_action}`}catch(e){message.textContent=e.message}}
function render(s){session.textContent=s.recorder.session_id||'';state.textContent=s.recorder.state;episode.textContent=s.recorder.episode_id||s.recorder.episode_path||'';const active=s.recorder.state==='RECORDING';start.disabled=active||s.preflight.blocked;success.disabled=!active;failed.disabled=!active;abort.disabled=!active;addAction.disabled=!active;cameraOff.disabled=active;cameraMosaic.disabled=active;cameraOff.classList.toggle('selected',s.camera_mode==='off');cameraMosaic.classList.toggle('selected',s.camera_mode==='mosaic');if(!primitive.options.length&&s.vla_annotation)(s.vla_annotation.primitives||[]).forEach(v=>primitive.add(new Option(v,v)));if(s.recorder.state==='RECORDING')setSignal('正在录制','recording',s.recorder.episode_id||'');else if(s.recorder.state==='FINALIZING')setSignal('正在保存和校验','finalizing','请勿关闭进程');else if(s.recorder.error)setSignal('录制器错误','error',s.recorder.error);else if(s.last_outcome&&s.last_outcome.aborted)setSignal('最近 Episode 已中止','error',s.last_outcome.path||'');else if(s.last_validation){const marked=s.last_outcome&&s.last_outcome.task_success?'成功标记':'失败标记';setSignal(s.last_validation.valid?`已保存：${marked}`:'最近 Episode 校验失败',s.last_validation.valid?(s.last_outcome&&s.last_outcome.task_success?'success':'failure'):'error',(s.last_validation.errors||[]).join('; ')||(s.last_outcome&&s.last_outcome.path)||'')}else if(s.preflight.blocked)setSignal('尚未就绪','error','请检查阻塞项');else setSignal('就绪，可以开始录制','success','');checks.innerHTML=s.preflight.checks.map(c=>`<div class="check"><strong class="${c.level}">${c.level}</strong><span>${c.name}: ${c.message}</span></div>`).join('');sources.innerHTML=Object.entries(s.sources||{}).map(([k,v])=>`<div class="metric"><span>${k}</span><span class="${v.level}">${v.message}</span></div>`).join('')}
async function poll(){try{const r=await fetch('/api/status');render(await r.json())}catch(e){state.textContent='DISCONNECTED'}setTimeout(poll,500)}poll();
</script></body></html>"""


class OperatorDashboard:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        get_status: Callable[[], dict[str, Any]],
        start_episode: Callable[[dict[str, Any]], dict[str, Any]],
        finish_episode: Callable[[dict[str, Any]], dict[str, Any]],
        abort_episode: Callable[[dict[str, Any]], dict[str, Any]],
        set_camera_mode: Callable[[dict[str, Any]], dict[str, Any]],
        add_language_action: Callable[[dict[str, Any]], dict[str, Any]],
    ):
        callbacks = {
            "/api/start": start_episode,
            "/api/finish": finish_episode,
            "/api/abort": abort_episode,
            "/api/camera-mode": set_camera_mode,
            "/api/language-action": add_language_action,
        }

        class Handler(BaseHTTPRequestHandler):
            def _send(self, status: int, content_type: str, data: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _json(self, status: int, value: object) -> None:
                self._send(status, "application/json; charset=utf-8", json.dumps(value).encode())

            def do_GET(self) -> None:
                path = urlparse(self.path).path
                if path == "/":
                    self._send(200, "text/html; charset=utf-8", _HTML.encode())
                elif path == "/api/status":
                    self._json(200, get_status())
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self) -> None:
                path = urlparse(self.path).path
                callback = callbacks.get(path)
                if callback is None:
                    self._json(404, {"error": "not found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    self._json(200, callback(payload))
                except Exception as exc:
                    self._json(409, {"error": str(exc)})

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, name="operator-dashboard", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=3.0)

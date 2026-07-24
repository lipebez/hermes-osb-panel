(function () {
  "use strict";

  /* ═══════════════════════════════════════════════════════════════
     OPEN SECOND BRAIN — Obsidian Graph Focus v3.1.0
     Canvas force-directed graph + 3-pane workspace + bottom info panel
     ═══════════════════════════════════════════════════════════════ */

  /* ── Mini React-ish runtime ── */
  var SVG_TAGS = { svg:1,defs:1,radialGradient:1,stop:1,circle:1,line:1,text:1,g:1,animate:1,animateTransform:1,path:1 };
  var _root = null, _cont = null, _hi = 0, _S = [], _D = [], _Fx = [], _C = [];

  function flat(a,o){a.forEach(function(x){if(Array.isArray(x))flat(x,o);else if(x!==false&&x!==true&&x!==null&&x!==undefined)o.push(x)});return o}
  function h(t,p){return{type:t,props:p||{},children:flat([].slice.call(arguments,2),[])}}
  function dc(p,n){if(!p||!n||p.length!==n.length)return true;for(var i=0;i<p.length;i++)if(p[i]!==n[i])return true;return false}
  function sp(el,k,v){
    if(k==="className")el.setAttribute("class",v||"");
    else if(k==="style"&&v&&typeof v==="object")Object.keys(v).forEach(function(s){el.style[s]=v[s]});
    else if(k==="dangerouslySetInnerHTML"&&v&&v.__html!==undefined)el.innerHTML=v.__html;
    else if(k.slice(0,2)==="on"&&typeof v==="function"){
      var eventName=k.slice(2).toLowerCase();
      if(eventName==="change"&&(el.tagName==="INPUT"||el.tagName==="TEXTAREA"))eventName="input";
      el.addEventListener(eventName,v);
    }
    else if(k==="value")el.value=v==null?"":v;
    else if(k==="disabled"||k==="checked"){if(v)el.setAttribute(k,k);else el.removeAttribute(k)}
    else if((k.slice(0,5)==="aria-"||k.slice(0,5)==="data-")&&v!==null&&v!==undefined)el.setAttribute(k,String(v));
    else if(v!==false&&v!==null&&v!==undefined)el.setAttribute(k,String(v));
  }
  function rn(v){
    if(typeof v==="string"||typeof v==="number")return document.createTextNode(String(v));
    if(Array.isArray(v)){var f=document.createDocumentFragment();v.forEach(function(c){f.appendChild(rn(c))});return f}
    if(!v||!v.type)return document.createTextNode("");
    if(typeof v.type==="function")return rn(v.type(Object.assign({},v.props,{children:v.children})));
    var el=SVG_TAGS[v.type]?document.createElementNS("http://www.w3.org/2000/svg",v.type):document.createElement(v.type);
    Object.keys(v.props||{}).forEach(function(k){
      if(k==="ref"&&v.props[k]&&typeof v.props[k]==="object")v.props[k].current=el;
      else if(k!=="children")sp(el,k,v.props[k]);
    });
    v.children.forEach(function(c){el.appendChild(rn(c))});
    return el;
  }
  function runCleanups(){_C.forEach(function(c){if(typeof c==="function"){try{c()}catch(e){console.error("effect cleanup",e)}}});_C=[]}
  function flushEffects(){_Fx.splice(0).forEach(function(item){var cleanup=item.fn();if(typeof cleanup==="function")_C[item.index]=cleanup})}
  function rr(){
    if(!_root||!_cont)return;
    var active=document.activeElement,focusId=active&&_cont.contains(active)?active.id:"";
    var focusName=active&&_cont.contains(active)?active.getAttribute("data-focus-key"):"";
    var selection=active&&typeof active.selectionStart==="number"?[active.selectionStart,active.selectionEnd]:null;
    runCleanups();_hi=0;_Fx=[];_cont.textContent="";_cont.appendChild(rn(h(_root)));flushEffects();
    var next=focusId?document.getElementById(focusId):(focusName?_cont.querySelector('[data-focus-key="'+focusName+'"]'):null);
    if(next){next.focus({preventScroll:true});if(selection&&next.setSelectionRange)next.setSelectionRange(selection[0],selection[1])}
  }
  var hooks={
    useState:function(i){var x=_hi++;if(_S[x]===undefined)_S[x]=i;return[_S[x],function(n){var next=typeof n==="function"?n(_S[x]):n;if(Object.is(next,_S[x]))return;_S[x]=next;rr()}]},
    useEffect:function(f,d){var x=_hi++;if(dc(_D[x],d)){_D[x]=d;_Fx.push({index:x,fn:f})}},
    useCallback:function(f,d){var x=_hi++;var m=_S[x];if(!m||dc(m.deps,d)){_S[x]={fn:f,deps:d};return f}return m.fn},
    useRef:function(v){var x=_hi++;if(_S[x]===undefined)_S[x]={current:v};return _S[x]}
  };
  function fbsdk(){
    return {
      React:{createElement:h},
      hooks:hooks,
      fetchJSON:function(u){return fetch(u,{credentials:"same-origin"}).then(function(r){if(!r.ok)throw new Error(r.status+" "+r.statusText);return r.json()})},
      mount:function(C,c){runCleanups();_root=C;_cont=c;rr()},
      unmount:function(){runCleanups();if(_cont)_cont.textContent="";_root=null;_cont=null}
    };
  }
  function fbreg(sdk){
    return {register:function(n,C){
      var m=function(){var c=document.getElementById("pluginPageContainer")||document.getElementById("osbStandaloneRoot");if(!c){c=document.createElement("div");c.id="osbStandaloneRoot";document.body.appendChild(c)}sdk.mount(C,c)};
      if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",m,{once:true});else m();
    }};
  }

  var SDK=window.__HERMES_PLUGIN_SDK__||fbsdk();
  var REG=window.__HERMES_PLUGINS__||fbreg(SDK);
  window.__HERMES_PLUGIN_SDK__=SDK;window.__HERMES_PLUGINS__=REG;

  var React=SDK.React, hooks2=SDK.hooks, fetchJSON=SDK.fetchJSON, h2=React.createElement;
  var API="/api/plugins/hermes-osb-panel/snapshot";

  /* ── Helpers ── */
  function asArray(v){return Array.isArray(v)?v:(v?[v]:[])}
  function trunc(s,n){s=String(s||"");return s.length>n?s.slice(0,n-1)+"…":s}
  function fmt(n){return (n||0).toLocaleString()}
  function cleanLabel(s){return String(s||"").replace(/—/g,"-").replace(/–/g,"-").replace(/\s+/g," ").trim()}
  /* Shared screen-space label placement. It measures actual glyph widths, chooses
     below/above the anchor, ellipsizes to the safe width, or returns null. */
  function labelSafeBounds(ctx,label,anchorX,preferredTop,alternateTop,safeRect){
    if(!isFinite(anchorX)||anchorX<safeRect.left||anchorX>safeRect.right)return null;
    var source=cleanLabel(label),maxWidth=Math.floor(Math.min(anchorX-safeRect.left,safeRect.right-anchorX)*2);
    if(!source||maxWidth<=0)return null;
    function ellipsize(value){
      if(ctx.measureText(value).width<=maxWidth)return value;
      var mark="…",markWidth=ctx.measureText(mark).width;if(markWidth>maxWidth)return "";
      var end=value.length;while(end>0&&ctx.measureText(value.slice(0,end)+mark).width>maxWidth)end--;
      return end?value.slice(0,end)+mark:mark;
    }
    var text=ellipsize(source);if(!text)return null;
    var metrics=ctx.measureText(text),ascent=metrics.actualBoundingBoxAscent||8,descent=metrics.actualBoundingBoxDescent||3,height=Math.ceil(ascent+descent)+2,width=Math.ceil(metrics.width),left=anchorX-width/2,right=anchorX+width/2;
    if(left<safeRect.left||right>safeRect.right)return null;
    var tops=[preferredTop,alternateTop];
    for(var i=0;i<tops.length;i++){var top=tops[i],bottom=top+height;if(isFinite(top)&&top>=safeRect.top&&bottom<=safeRect.bottom)return{text:text,left:left,right:right,top:top,bottom:bottom,baseline:top+ascent+1};}
    return null;
  }
  function labelBoundsSnapshot(bounds){return bounds.map(function(b){return{left:b.left,right:b.right,top:b.top,bottom:b.bottom}})}
  function obsidianLink(provider,node){
    var vault=provider&&provider.obsidian_vault_id,id=node&&node.id;
    if(!vault||!id||id.startsWith("/"))return "";
    return "obsidian://open?vault="+encodeURIComponent(vault)+"&file="+encodeURIComponent(id.replace(/\.md$/,"").replace(/^\//,""));
  }
  function uiIcon(name,size){
    var paths={
      brain:["M10 4H9a3 3 0 0 0-3 3v1a3 3 0 0 0-2 2.8V13a3 3 0 0 0 3 3h3Z","M14 4h1a3 3 0 0 1 3 3v1a3 3 0 0 1 2 2.8V13a3 3 0 0 1-3 3h-3Z","M10 8H8M14 8h2M10 12H8M14 12h2"],
      panel:["M4 4h16v16H4Z","M9 4v16"],
      frame:["M8 4H4v4M16 4h4v4M8 20H4v-4M16 20h4v-4"],
      target:["M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z","M12 3v3M12 18v3M3 12h3M18 12h3"],
      copy:["M8 8h11v11H8Z","M16 8V5H5v11h3"],
      link:["M10 13a5 5 0 0 0 7.54.54l2-2a5 5 0 0 0-7.07-7.07l-1.15 1.15","M14 11a5 5 0 0 0-7.54-.54l-2 2a5 5 0 0 0 7.07 7.07l1.15-1.15"],
      left:["m15 18-6-6 6-6"],
      down:["m6 9 6 6 6-6"]
    };
    return h2("svg",{className:"ui-icon",width:size||14,height:size||14,viewBox:"0 0 24 24",fill:"none",stroke:"currentColor","stroke-width":"1.8","stroke-linecap":"round","stroke-linejoin":"round","aria-hidden":"true"},
      (paths[name]||paths.frame).map(function(d,i){return h2("path",{d:d,key:name+i})})
    );
  }

  /* Area-based color system — each area gets a distinct, distinguishable color */
  function areaColor(area){
    var c={
      brain:"#bc8cff",
      inbox:"#3fb950",
      projects:"#58a6ff",
      clients:"#e3b341",
      runbooks:"#d2a8ff",
      decisions:"#ff7b72",
      references:"#79c0ff",
      templates:"#7a7fad",
      other:"#8b949e"
    };
    return c[area]||c.other;
  }
  /* Kind-based fallback for brain nodes */
  function kindColor(kind){
    var c={
      active:"#f7f8f8",preference:"#bc8cff",signal:"#3fb950",log:"#7a7fad",
      retired:"#62666d",note:"#58a6ff"
    };
    return c[kind]||c.note;
  }
  /* Master color resolver: brain nodes use kind, vault nodes use area */
  function nodeColor(node){
    if(node.layer==="brain")return kindColor(node.kind);
    return areaColor(node.area||"other");
  }
  function kindLabel(k){
    var l={active:"Active",preference:"Preferência",signal:"Sinal",log:"Log",retired:"Retired",note:"Nota",vault:"Vault",brain:"Brain"};
    return l[k]||k||"Nota";
  }
  function areaLabel(a){
    var l={brain:"Brain",inbox:"Inbox",projects:"Projetos",clients:"Clientes",runbooks:"Runbooks",decisions:"Decisões",references:"Referências",templates:"Templates",other:"Outros"};
    return l[a]||a||"Outros";
  }
  function areaDotClass(area){
    return "dot-"+(area||"other");
  }

  /* ═══════════════════════════════════════════════════════════════
     GRAPH CANVAS ENGINE — Force-directed with area-colored nodes
     ═══════════════════════════════════════════════════════════════ */
  function GraphCanvas(props) {
    var nodes = props.nodes || [];
    var edges = props.edges || [];
    var selected = props.selected;
    var onSelect = props.onSelect;
    var canvasRef = hooks2.useRef(null);
    var sim = hooks2.useRef(null);

    hooks2.useEffect(function () {
      var canvas = canvasRef.current;
      if (!canvas) return;
      var ctx = canvas.getContext("2d");
      var W = 0, H = 0, zoom = 1, minZoom = 0.4, maxZoom = 3, labelSafePadding = 24, labelBounds = [];
      var panX = 0, panY = 0, isPanning = false, lastPX = 0, lastPY = 0;
      var dragNode = null, hoverNode = null;
      var dpr = window.devicePixelRatio || 1;
      resize();

      var prev = sim.current;
      if (prev && prev.raf) cancelAnimationFrame(prev.raf);
      var snodes = nodes.map(function (n, i) {
        var label = cleanLabel(n.label);
        if (prev && prev.nodeMap[n.id] && prev.nodes && prev.nodes.length === nodes.length) {
          var old = prev.nodeMap[n.id];
          return { id: n.id, label: label, kind: n.kind, tone: n.tone, layer: n.layer, area: n.area, degree: n.degree || 0, hub: !!n.hub, ghost: !!n.ghost, x: old.x, y: old.y, vx: old.vx, vy: old.vy, size: n.size || 8, color: nodeColor(n) };
        }
        var cx = W / 2 || 400, cy = H / 2 || 300;
        var angle = (Math.PI * 2 * i) / Math.max(nodes.length, 1);
        var radius = 80 + Math.random() * 120;
        return { id: n.id, label: label, kind: n.kind, tone: n.tone, layer: n.layer, area: n.area, degree: n.degree || 0, hub: !!n.hub, ghost: !!n.ghost, x: cx + Math.cos(angle) * radius, y: cy + Math.sin(angle) * radius, vx: 0, vy: 0, size: n.size || 8, color: nodeColor(n) };
      });
      var nodeMap = {};
      snodes.forEach(function (n) { nodeMap[n.id] = n; });
      var sedges = edges.filter(function (e) { return nodeMap[e.source] && nodeMap[e.target]; });
      sim.current = { nodes: snodes, edges: sedges, nodeMap: nodeMap, raf: null, alpha: 1, stableFrames: 0, energy: 1 };
      var destroyed = false;
      var resizeTimer = null;

      function resize() {
        var rect = canvas.getBoundingClientRect();
        W = rect.width; H = rect.height;
        labelSafePadding=Math.max(16,Math.min(40,Math.min(W,H)*.08));
        canvas.width = W * dpr; canvas.height = H * dpr;
        ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.scale(dpr, dpr);
        if (sim.current) reheat(0.7);
      }

      function reheat(amount) {
        if (destroyed || !sim.current) return;
        sim.current.alpha = Math.max(sim.current.alpha || 0, amount || 0.65);
        sim.current.stableFrames = 0;
        if (!sim.current.raf) sim.current.raf = requestAnimationFrame(animate);
      }

      function fitNodes(targets) {
        targets = (targets || []).filter(Boolean);
        if (!targets.length || !W || !H) return;
        var minX=Math.min.apply(null,targets.map(function(n){return n.x}));
        var maxX=Math.max.apply(null,targets.map(function(n){return n.x}));
        var minY=Math.min.apply(null,targets.map(function(n){return n.y}));
        var maxY=Math.max.apply(null,targets.map(function(n){return n.y}));
        var spanX=Math.max(80,maxX-minX+80),spanY=Math.max(80,maxY-minY+80);
        zoom=Math.max(minZoom,Math.min(maxZoom,Math.min(W/spanX,H/spanY)*0.9));
        var centerX=(minX+maxX)/2,centerY=(minY+maxY)/2;
        panX=-zoom*(centerX-W/2);panY=-zoom*(centerY-H/2);
        draw();reheat(0.25);
      }

      function simulate() {
        var ns = sim.current.nodes;
        var alpha = sim.current.alpha;
        var repulsion = 1800 * alpha;
        for (var i = 0; i < ns.length; i++) {
          for (var j = i + 1; j < ns.length; j++) {
            var dx = ns[j].x - ns[i].x, dy = ns[j].y - ns[i].y;
            var d = Math.sqrt(dx * dx + dy * dy) || 1;
            var f = repulsion / (d * d);
            if (!ns[i].drag) { ns[i].vx -= (dx / d) * f; ns[i].vy -= (dy / d) * f; }
            if (!ns[j].drag) { ns[j].vx += (dx / d) * f; ns[j].vy += (dy / d) * f; }
          }
        }
        sim.current.edges.forEach(function (e) {
          var n1 = nodeMap[e.source], n2 = nodeMap[e.target];
          if (!n1 || !n2) return;
          var dx = n2.x - n1.x, dy = n2.y - n1.y;
          var d = Math.sqrt(dx * dx + dy * dy) || 1;
          var f = (d - 120) * 0.015 * alpha;
          if (!n1.drag) { n1.vx += (dx / d) * f; n1.vy += (dy / d) * f; }
          if (!n2.drag) { n2.vx -= (dx / d) * f; n2.vy -= (dy / d) * f; }
        });
        var cx = W / 2, cy = H / 2;
        ns.forEach(function (n) {
          if (n.drag) return;
          n.vx += (cx - n.x) * 0.0015 * alpha; n.vy += (cy - n.y) * 0.0015 * alpha;
          n.vx *= 0.82; n.vy *= 0.82; n.x += n.vx; n.y += n.vy;
        });
        sim.current.energy = ns.reduce(function(total,n){return total+Math.abs(n.vx)+Math.abs(n.vy)},0)/Math.max(1,ns.length);
        sim.current.alpha *= 0.955;
        if (sim.current.alpha < 0.012 && sim.current.energy < 0.018) sim.current.stableFrames += 1;
        else sim.current.stableFrames = 0;
      }

      function draw() {
        ctx.clearRect(0, 0, W, H);
        ctx.save();
        ctx.translate(W / 2 + panX, H / 2 + panY);
        ctx.scale(zoom, zoom);
        ctx.translate(-W / 2, -H / 2);

        var ns = sim.current.nodes;
        var connected = new Set();
        var selectedNode = selected ? sim.current.nodeMap[selected.id] : null;
        var hi = hoverNode || selectedNode;
        var focusDimming = !!hoverNode;
        if (hi) {
          connected.add(hi.id);
          sim.current.edges.forEach(function (e) {
            if (e.source === hi.id) connected.add(e.target);
            if (e.target === hi.id) connected.add(e.source);
          });
        }

        /* Edges */
        sim.current.edges.forEach(function (e) {
          var n1 = nodeMap[e.source], n2 = nodeMap[e.target];
          if (!n1 || !n2) return;
          ctx.beginPath();
          ctx.moveTo(n1.x, n1.y); ctx.lineTo(n2.x, n2.y);
          if (hi && connected.has(n1.id) && connected.has(n2.id)) {
            ctx.strokeStyle = e.kind === "wikilink" ? "rgba(188,140,255,0.8)" : "rgba(88,166,255,0.6)";
            ctx.lineWidth = 1.8;
          } else if (focusDimming) {
            ctx.strokeStyle = "rgba(139,148,158,0.03)"; ctx.lineWidth = 1;
          } else {
            ctx.strokeStyle = e.kind === "wikilink" ? "rgba(188,140,255,0.15)" : "rgba(139,148,158,0.1)";
            ctx.lineWidth = 1;
          }
          ctx.stroke();
        });

        /* Nodes: filtered nodes stay opaque; only explicit ghosts are translucent. */
        ns.forEach(function (n) {
          var isHi=n===hi,isConn=connected.has(n.id);
          var alpha=n.ghost?0.34:(focusDimming?(isConn?1:0.12):1);
          if(isHi||(focusDimming&&isConn)){
            ctx.beginPath();ctx.arc(n.x,n.y,n.size+8,0,Math.PI*2);ctx.fillStyle=n.color+"25";ctx.fill();
          }
          ctx.beginPath();ctx.arc(n.x,n.y,n.size,0,Math.PI*2);ctx.fillStyle=n.color;ctx.globalAlpha=alpha;ctx.fill();
          if(isHi){ctx.strokeStyle="#fff";ctx.lineWidth=2;ctx.stroke()}
          ctx.globalAlpha=1;
        });

        ctx.restore();

        /* Labels use the shared measured screen-space safe bounds, after pan/zoom. */
        var occupied=[],safeRect={left:labelSafePadding,right:W-labelSafePadding,top:labelSafePadding,bottom:H-labelSafePadding};
        labelBounds=[];
        var ranked=ns.slice().sort(function(a,b){
          function priority(n){return n===hi?10000:(n.hub?5000:0)+(connected.has(n.id)?2500:0)+(n.degree||0)*20+(n.layer==="vault"?80:0)}
          return priority(b)-priority(a)||a.label.localeCompare(b.label);
        });
        ranked.forEach(function(n){
          var isHi=n===hi,isConn=connected.has(n.id);
          var show=isHi||isConn||n.hub||(zoom>=0.72&&(n.degree||0)>=4)||(zoom>=1.08&&n.layer==="vault")||zoom>=1.45;
          if(n.ghost&&!isHi&&!isConn&&zoom<1.25)show=false;
          if(!show)return;
          var sx=W/2+panX+zoom*(n.x-W/2),sy=H/2+panY+zoom*(n.y-H/2),radius=Math.max(2,n.size*zoom);
          ctx.font=(isHi?"bold ":"")+"11px Inter, sans-serif";ctx.textAlign="center";
          var placement=labelSafeBounds(ctx,n.label,sx,sy+radius+6,sy-radius-16,safeRect);
          if(!placement)return;
          var collision=!isHi&&!isConn&&occupied.some(function(o){return!(placement.right<o.left||placement.left>o.right||placement.bottom<o.top||placement.top>o.bottom)});
          if(collision)return;occupied.push(placement);labelBounds.push(placement);
          ctx.fillStyle=isHi?"#fff":"#c9d1d9";ctx.globalAlpha=n.ghost?0.58:1;ctx.fillText(placement.text,sx,placement.baseline);ctx.globalAlpha=1;
        });
      }

      function animate() {
        if (destroyed || !sim.current) return;
        sim.current.raf = null;
        simulate(); draw();
        if (sim.current.stableFrames < 8) sim.current.raf = requestAnimationFrame(animate);
      }

      function screenToGraph(x, y) {
        return { x: W / 2 + (x - W / 2 - panX) / zoom, y: H / 2 + (y - H / 2 - panY) / zoom };
      }

      function onPointerDown(e) {
        if (canvas.setPointerCapture) canvas.setPointerCapture(e.pointerId);
        var rect = canvas.getBoundingClientRect();
        var p = screenToGraph(e.clientX - rect.left, e.clientY - rect.top);
        var hit = null;
        sim.current.nodes.forEach(function (n) {
          var dx = p.x - n.x, dy = p.y - n.y;
          if (Math.sqrt(dx * dx + dy * dy) < n.size + 4) hit = n;
        });
        if (hit) { dragNode = hit; hit.drag = true; }
        else { isPanning = true; lastPX = e.clientX; lastPY = e.clientY; }
        reheat(0.85);
      }
      function onPointerMove(e) {
        var rect = canvas.getBoundingClientRect();
        var p = screenToGraph(e.clientX - rect.left, e.clientY - rect.top);
        if (dragNode) { dragNode.x = p.x; dragNode.y = p.y; dragNode.vx = 0; dragNode.vy = 0; reheat(0.55); }
        else if (isPanning) { panX += e.clientX - lastPX; panY += e.clientY - lastPY; lastPX = e.clientX; lastPY = e.clientY; draw(); }
        else {
          hoverNode = null;
          sim.current.nodes.forEach(function (n) {
            var dx = p.x - n.x, dy = p.y - n.y;
            if (Math.sqrt(dx * dx + dy * dy) < n.size + 4) { hoverNode = n; canvas.style.cursor = "pointer"; }
          });
          if (!hoverNode) canvas.style.cursor = "grab";
        }
      }
      function onPointerUp(e) {
        if (dragNode) {
          if (onSelect) { var sn = nodes.find(function (nn) { return nn.id === dragNode.id; }); if (sn) onSelect(sn); }
          dragNode.drag = false; dragNode = null;
        }
        isPanning = false; reheat(0.35);
        if (canvas.releasePointerCapture && canvas.hasPointerCapture && canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
      }
      function onPointerLeave() {
        if (dragNode) { dragNode.drag = false; dragNode = null; }
        isPanning = false; hoverNode = null;
        draw();
      }
      function onWheel(e) {
        e.preventDefault();
        var delta = e.deltaY > 0 ? 0.9 : 1.1;
        zoom = Math.max(minZoom, Math.min(maxZoom, zoom * delta));
        draw(); reheat(0.25);
      }
      canvas.addEventListener("pointerdown", onPointerDown);
      canvas.addEventListener("pointermove", onPointerMove);
      canvas.addEventListener("pointerup", onPointerUp);
      canvas.addEventListener("pointercancel", onPointerUp);
      canvas.addEventListener("pointerleave", onPointerLeave);
      canvas.addEventListener("wheel", onWheel, { passive: false });

      var parent = canvas.parentElement;
      var controls = parent.querySelector("[data-graph-controls]");
      function onControlClick(ev) {
          var btn = ev.target.closest("[data-graph-control]");
          if (!btn) return;
          var action = btn.dataset.graphControl;
          if (action === "zoom-in") zoom = Math.min(maxZoom, zoom * 1.2);
          else if (action === "zoom-out") zoom = Math.max(minZoom, zoom / 1.2);
          else if (action === "reset") { zoom = 1; panX = 0; panY = 0; }
          else if (action === "fit-all") fitNodes(sim.current.nodes);
          else if (action === "fit-selection") {
            var chosen=selected&&nodeMap[selected.id];
            var related=chosen?[chosen].concat(sim.current.edges.filter(function(e){return e.source===chosen.id||e.target===chosen.id}).map(function(e){return nodeMap[e.source===chosen.id?e.target:e.source]})):sim.current.nodes;
            fitNodes(related);
          }
          draw(); reheat(0.35);
      }
      if (controls) controls.addEventListener("click", onControlClick);

      var graphBridge={
        fitAll:function(){fitNodes(sim.current.nodes)},
        fitSelection:function(){
          var chosen=selected&&nodeMap[selected.id];
          if(!chosen)return fitNodes(sim.current.nodes);
          fitNodes([chosen].concat(sim.current.edges.filter(function(e){return e.source===chosen.id||e.target===chosen.id}).map(function(e){return nodeMap[e.source===chosen.id?e.target:e.source]})));
        },
        overview:function(){zoom=1;panX=0;panY=0;fitNodes(sim.current.nodes)},
        reheat:function(){reheat(0.8)},
        state:function(){return{mode:"2d",alpha:sim.current.alpha,energy:sim.current.energy,running:!!sim.current.raf,zoom:zoom,viewport:[W,H],labelSafePadding:labelSafePadding,labelBounds:labelBoundsSnapshot(labelBounds)}}
      };
      window.__OSB_GRAPH__=graphBridge;

      window.addEventListener("resize", resize);
      resize(); resizeTimer = setTimeout(resize, 100); reheat(1);
      return function () {
        destroyed = true;
        window.removeEventListener("resize", resize);
        if (resizeTimer) clearTimeout(resizeTimer);
        if (sim.current && sim.current.raf) cancelAnimationFrame(sim.current.raf);
        canvas.removeEventListener("pointerdown", onPointerDown);
        canvas.removeEventListener("pointermove", onPointerMove);
        canvas.removeEventListener("pointerup", onPointerUp);
        canvas.removeEventListener("pointercancel", onPointerUp);
        canvas.removeEventListener("pointerleave", onPointerLeave);
        canvas.removeEventListener("wheel", onWheel);
        if (controls) controls.removeEventListener("click", onControlClick);
        if(window.__OSB_GRAPH__===graphBridge)delete window.__OSB_GRAPH__;
      };
    });

    return h2("div", { className: "graph-pane" },
      h2("canvas", { ref: canvasRef, id: "graphCanvas" }),
      h2("div", { className: "graph-controls", "data-graph-controls": true },
        h2("button", { className: "control-btn", title: "Aumentar zoom", "data-tooltip":"Aumentar zoom", "aria-label":"Aumentar zoom", "data-graph-control": "zoom-in" }, "+"),
        h2("button", { className: "control-btn", title: "Diminuir zoom", "data-tooltip":"Diminuir zoom", "aria-label":"Diminuir zoom", "data-graph-control": "zoom-out" }, "−"),
        h2("button", { className: "control-btn", title: "Enquadrar todos os nós", "data-tooltip":"Enquadrar tudo", "aria-label":"Enquadrar todos os nós", "data-graph-control": "fit-all" }, uiIcon("frame",15)),
        h2("button", { className: "control-btn", title: selected?"Focar a nota e conexões diretas":"Selecione uma nota para focar", "data-tooltip":"Focar seleção", "aria-label":"Focar a nota e conexões diretas", disabled:!selected, "data-graph-control": "fit-selection" }, uiIcon("target",15))
      ),
      h2("div", { className: "graph-info" },
        h2("span", null, "NÓS: ", h2("span", { className: "val" }, fmt(nodes.length))),
        h2("span", null, "LINKS: ", h2("span", { className: "val" }, fmt(edges.length))),
        h2("span", null, selected ? "▸ " + trunc(cleanLabel(selected.label), 18) : "Passe o mouse")
      )
    );
  }

  /* True 3D force scene projected into a dependency-free Canvas 2D surface. */
  function GraphCanvas3D(props) {
    var nodes=props.nodes||[],edges=props.edges||[],selected=props.selected,onSelect=props.onSelect;
    var reducedMotion=!!(window.matchMedia&&window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    var canvasRef=hooks2.useRef(null);
    hooks2.useEffect(function(){
      var canvas=canvasRef.current;if(!canvas)return;
      var ctx=canvas.getContext("2d"),dpr=Math.min(2,window.devicePixelRatio||1),W=0,H=0,raf=0,destroyed=false,loopActive=false;
      var yaw=-0.58,pitch=-0.24,distance=540,focal=560,target={x:0,y:0,z:0},alpha=1,cameraTouched=false;
      var dragging=false,moved=0,lastX=0,lastY=0,hover=null,projected=[],safePadding=32,labelBounds=[];
      var autoRotate=!reducedMotion;
      var diagnostics=window.__OSB_GRAPH_DIAGNOSTICS__||(window.__OSB_GRAPH_DIAGNOSTICS__={mounts:0,cleanups:0,activeRafs:0,activeListenerSets:0});
      diagnostics.mounts++;diagnostics.activeListenerSets++;
      function hash(value){var h=2166136261,s=String(value||"");for(var i=0;i<s.length;i++){h^=s.charCodeAt(i);h=Math.imul(h,16777619)}return h>>>0}
      var snodes=nodes.map(function(n,i){
        var t=(i+.5)/Math.max(1,nodes.length),phi=Math.acos(1-2*t),theta=Math.PI*(1+Math.sqrt(5))*i+(hash(n.id)%1000)/1000;
        var radius=145+Math.min(95,(n.degree||0)*5)+(hash(n.id+"z")%42);
        return{id:n.id,label:cleanLabel(n.label),kind:n.kind,layer:n.layer,area:n.area,degree:n.degree||0,hub:!!n.hub,ghost:!!n.ghost,size:n.size||8,color:nodeColor(n),x:Math.sin(phi)*Math.cos(theta)*radius,y:Math.cos(phi)*radius,z:Math.sin(phi)*Math.sin(theta)*radius,vx:0,vy:0,vz:0};
      });
      var nodeMap={};snodes.forEach(function(n){nodeMap[n.id]=n});
      var sedges=edges.filter(function(e){return nodeMap[e.source]&&nodeMap[e.target]});
      var connected=new Set(),initialized=false;
      function currentSceneRadius(center){center=center||target;return snodes.reduce(function(m,n){var dx=n.x-center.x,dy=n.y-center.y,dz=n.z-center.z;return Math.max(m,Math.sqrt(dx*dx+dy*dy+dz*dz)+(n.size||8)+20)},180)}
      function fitDistance(center){var half=Math.max(52,Math.min(W,H)/2-safePadding),radius=currentSceneRadius(center);return Math.max(430,Math.min(4000,radius*(1.12+focal/half)))}
      function stopLoop(){if(raf){cancelAnimationFrame(raf);raf=0}if(loopActive){loopActive=false;diagnostics.activeRafs=Math.max(0,diagnostics.activeRafs-1)}}
      function schedule(){if(destroyed||raf)return;if(!loopActive){loopActive=true;diagnostics.activeRafs++}raf=requestAnimationFrame(animate)}
      function wake(amount){alpha=Math.max(alpha,amount||.08);schedule()}
      function resize(){var r=canvas.getBoundingClientRect();W=Math.max(1,r.width);H=Math.max(1,r.height);safePadding=Math.max(22,Math.min(64,Math.min(W,H)*.1));canvas.width=Math.round(W*dpr);canvas.height=Math.round(H*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);if(!initialized){distance=fitDistance({x:0,y:0,z:0});initialized=true}else if(!cameraTouched){distance=fitDistance(target)}wake(.18)}
      function projectPoint(x,y,z){
        x-=target.x;y-=target.y;z-=target.z;
        var cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);
        var x1=x*cy-z*sy,z1=x*sy+z*cy,y1=y;
        var y2=y1*cp-z1*sp,z2=y1*sp+z1*cp,depth=Math.max(35,distance+z2),scale=focal/depth;
        return{x:W/2+x1*scale,y:H/2+y2*scale,z:z2,depth:depth,scale:scale};
      }
      function connections(){connected.clear();var id=(hover&&hover.id)||(selected&&selected.id);if(!id)return;connected.add(id);sedges.forEach(function(e){if(e.source===id)connected.add(e.target);if(e.target===id)connected.add(e.source)})}
      function simulate(){
        if(alpha<.01)return;
        var repulsion=2100*alpha;
        for(var i=0;i<snodes.length;i++)for(var j=i+1;j<snodes.length;j++){
          var a=snodes[i],b=snodes[j],dx=b.x-a.x,dy=b.y-a.y,dz=b.z-a.z,d2=dx*dx+dy*dy+dz*dz+1,d=Math.sqrt(d2),f=repulsion/d2;
          a.vx-=dx/d*f;a.vy-=dy/d*f;a.vz-=dz/d*f;b.vx+=dx/d*f;b.vy+=dy/d*f;b.vz+=dz/d*f;
        }
        sedges.forEach(function(e){var a=nodeMap[e.source],b=nodeMap[e.target],dx=b.x-a.x,dy=b.y-a.y,dz=b.z-a.z,d=Math.sqrt(dx*dx+dy*dy+dz*dz)||1,f=(d-105)*.01*alpha;a.vx+=dx/d*f;a.vy+=dy/d*f;a.vz+=dz/d*f;b.vx-=dx/d*f;b.vy-=dy/d*f;b.vz-=dz/d*f});
        snodes.forEach(function(n){n.vx+=-n.x*.0018*alpha;n.vy+=-n.y*.0018*alpha;n.vz+=-n.z*.0018*alpha;n.vx*=.84;n.vy*=.84;n.vz*=.84;n.x+=n.vx;n.y+=n.vy;n.z+=n.vz});
        alpha*=.952;if(!cameraTouched)distance=Math.max(distance,fitDistance(target));
      }
      function backdrop(){
        var bg=ctx.createRadialGradient(W*.5,H*.45,0,W*.5,H*.5,Math.max(W,H)*.7);bg.addColorStop(0,"#132032");bg.addColorStop(.45,"#0b121d");bg.addColorStop(1,"#060910");ctx.fillStyle=bg;ctx.fillRect(0,0,W,H);
        if(!reducedMotion){ctx.save();ctx.fillStyle="rgba(121,192,255,.28)";for(var i=0;i<78;i++){var sx=(hash("sx"+i)%10000)/10000*W,sy=(hash("sy"+i)%10000)/10000*H,sr=i%11===0?1.2:.55;ctx.globalAlpha=.18+(i%7)*.035;ctx.fillRect(sx,sy,sr,sr)}ctx.restore()}
        ctx.save();ctx.strokeStyle="rgba(88,166,255,.07)";ctx.lineWidth=1;for(var ring=100;ring<=300;ring+=50){ctx.beginPath();for(var k=0;k<=64;k++){var a=Math.PI*2*k/64,p=projectPoint(Math.cos(a)*ring,0,Math.sin(a)*ring);if(k)ctx.lineTo(p.x,p.y);else ctx.moveTo(p.x,p.y)}ctx.stroke()}ctx.restore();
      }
      function draw(){
        backdrop();connections();projected=snodes.map(function(n){return{n:n,p:projectPoint(n.x,n.y,n.z)}});
        var hi=hover||(selected&&nodeMap[selected.id]),focus=!!hover;
        sedges.forEach(function(e){var a=nodeMap[e.source],b=nodeMap[e.target],pa=projectPoint(a.x,a.y,a.z),pb=projectPoint(b.x,b.y,b.z),hot=hi&&connected.has(a.id)&&connected.has(b.id),fog=Math.max(.28,Math.min(1,(1100-(pa.depth+pb.depth)/2)/520));ctx.beginPath();ctx.moveTo(pa.x,pa.y);ctx.lineTo(pb.x,pb.y);ctx.strokeStyle=hot?(e.kind==="wikilink"?"rgba(188,140,255,.86)":"rgba(88,166,255,.78)"):(focus?"rgba(90,110,138,.04)":(e.kind==="wikilink"?"rgba(188,140,255,.32)":"rgba(88,166,255,.28)"));ctx.globalAlpha=hot?1:fog;ctx.lineWidth=hot?1.5:.8;ctx.stroke()});ctx.globalAlpha=1;
        var ordered=projected.slice().sort(function(a,b){return b.p.depth-a.p.depth}),labels=0,occupied=[],safeRect={left:safePadding,right:W-safePadding,top:safePadding,bottom:H-safePadding};labelBounds=[];
        ordered.forEach(function(item){var n=item.n,p=item.p;if(p.depth<=40)return;var isHi=n===hi,isConn=connected.has(n.id),fog=Math.max(.22,Math.min(1,(920-p.depth)/520)),opacity=n.ghost?.3:(focus?(isConn?1:.1):fog),r=Math.max(2.6,Math.min(19,(n.size+2)*p.scale));
          if(isHi||isConn){ctx.beginPath();ctx.arc(p.x,p.y,r+8,0,Math.PI*2);ctx.fillStyle=n.color+"20";ctx.globalAlpha=opacity;ctx.fill()}
          ctx.save();ctx.globalAlpha=opacity;ctx.shadowColor=n.color;ctx.shadowBlur=isHi?22:Math.max(4,11*p.scale);var g=ctx.createRadialGradient(p.x-r*.34,p.y-r*.38,Math.max(.5,r*.08),p.x,p.y,r);g.addColorStop(0,"rgba(255,255,255,.96)");g.addColorStop(.22,n.color);g.addColorStop(1,"rgba(4,8,16,.96)");ctx.fillStyle=g;ctx.beginPath();ctx.arc(p.x,p.y,r,0,Math.PI*2);ctx.fill();if(isHi){ctx.shadowBlur=0;ctx.strokeStyle="#fff";ctx.lineWidth=1.8;ctx.stroke()}ctx.restore();
          var show=isHi||isConn||n.hub||(n.degree>=9&&p.scale>.62);if(!show||labels>18)return;ctx.font=(isHi?"600 ":"500 ")+"10px Inter, sans-serif";ctx.textAlign="center";var placement=labelSafeBounds(ctx,n.label,p.x,p.y+r+5,p.y-r-18,safeRect);if(!placement)return;if(!isHi&&occupied.some(function(o){return!(placement.right<o.left||placement.left>o.right||placement.bottom<o.top||placement.top>o.bottom)}))return;occupied.push(placement);labelBounds.push(placement);labels++;ctx.globalAlpha=opacity;ctx.fillStyle=isHi?"#fff":"#c9d1d9";ctx.fillText(placement.text,p.x,placement.baseline);ctx.globalAlpha=1;
        });
        var vignette=ctx.createRadialGradient(W/2,H/2,Math.min(W,H)*.22,W/2,H/2,Math.max(W,H)*.72);vignette.addColorStop(.55,"rgba(0,0,0,0)");vignette.addColorStop(1,"rgba(0,0,0,.48)");ctx.fillStyle=vignette;ctx.fillRect(0,0,W,H);
      }
      function animate(){if(destroyed)return;raf=0;simulate();if(autoRotate&&!dragging)yaw+=.00075;draw();if(autoRotate||dragging||alpha>=.01)raf=requestAnimationFrame(animate);else stopLoop()}
      function hitAt(x,y){var hit=null,best=1e9;projected.forEach(function(item){var r=Math.max(5,Math.min(22,(item.n.size+4)*item.p.scale)),dx=x-item.p.x,dy=y-item.p.y,d=dx*dx+dy*dy;if(d<r*r&&item.p.depth<best){hit=item.n;best=item.p.depth}});return hit}
      function setAutoRotate(next,button){if(reducedMotion)return;autoRotate=!!next;if(button)button.setAttribute("aria-pressed",autoRotate?"true":"false");wake(.04)}
      function pointerDown(e){dragging=true;moved=0;lastX=e.clientX;lastY=e.clientY;cameraTouched=true;setAutoRotate(false,canvas.parentElement.querySelector('[data-graph-control="toggle-orbit"]'));if(canvas.setPointerCapture)canvas.setPointerCapture(e.pointerId);schedule()}
      function pointerMove(e){var r=canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;if(dragging){var dx=e.clientX-lastX,dy=e.clientY-lastY;yaw+=dx*.007;pitch=Math.max(-1.25,Math.min(1.25,pitch+dy*.006));moved+=Math.abs(dx)+Math.abs(dy);lastX=e.clientX;lastY=e.clientY;schedule()}else{var next=hitAt(x,y);if(next!==hover){hover=next;draw()}canvas.style.cursor=hover?"pointer":"grab"}}
      function pointerUp(e){var r=canvas.getBoundingClientRect(),hit=hitAt(e.clientX-r.left,e.clientY-r.top);if(moved<6&&hit&&onSelect){var source=nodes.find(function(n){return n.id===hit.id});if(source)onSelect(source)}dragging=false;if(canvas.releasePointerCapture&&canvas.hasPointerCapture&&canvas.hasPointerCapture(e.pointerId))canvas.releasePointerCapture(e.pointerId);wake(.03)}
      function wheel(e){e.preventDefault();cameraTouched=true;distance=Math.max(250,Math.min(4000,distance*(e.deltaY>0?1.1:.9)));wake(.03)}
      function fitAll(){target={x:0,y:0,z:0};cameraTouched=false;distance=fitDistance(target);yaw=-.58;pitch=-.24;wake(.2)}
      function fitSelection(){var n=selected&&nodeMap[selected.id];if(!n)return fitAll();target={x:n.x,y:n.y,z:n.z};cameraTouched=true;distance=fitDistance(target);wake(.15)}
      function control(e){var b=e.target.closest("[data-graph-control]");if(!b)return;var a=b.dataset.graphControl;if(a==="zoom-in"){cameraTouched=true;distance=Math.max(250,distance*.9);wake(.03)}else if(a==="zoom-out"){cameraTouched=true;distance=Math.min(4000,distance*1.12);wake(.03)}else if(a==="fit-selection")fitSelection();else if(a==="toggle-orbit")setAutoRotate(!autoRotate,b);else fitAll()}
      var controls=canvas.parentElement.querySelector("[data-graph-controls]");canvas.addEventListener("pointerdown",pointerDown);canvas.addEventListener("pointermove",pointerMove);canvas.addEventListener("pointerup",pointerUp);canvas.addEventListener("pointercancel",pointerUp);canvas.addEventListener("wheel",wheel,{passive:false});if(controls)controls.addEventListener("click",control);window.addEventListener("resize",resize);
      var bridge={fitAll:fitAll,fitSelection:fitSelection,overview:fitAll,reheat:function(){wake(.8)},orbit:function(dx,dy){cameraTouched=true;yaw+=dx||0;pitch=Math.max(-1.25,Math.min(1.25,pitch+(dy||0)));setAutoRotate(false,controls&&controls.querySelector('[data-graph-control="toggle-orbit"]'));wake(.04)},state:function(){var xs=projected.map(function(i){return i.p.x}),ys=projected.map(function(i){return i.p.y});return{mode:"3d",yaw:yaw,pitch:pitch,distance:distance,autoRotate:autoRotate,reducedMotion:reducedMotion,running:loopActive,painted:projected.length,viewport:[W,H],safePadding:safePadding,labelSafePadding:safePadding,labelBounds:labelBoundsSnapshot(labelBounds),bounds:projected.length?{left:Math.min.apply(null,xs),right:Math.max.apply(null,xs),top:Math.min.apply(null,ys),bottom:Math.max.apply(null,ys)}:null,activeRafs:diagnostics.activeRafs,activeListenerSets:diagnostics.activeListenerSets}}};window.__OSB_GRAPH__=bridge;
      resize();schedule();
      return function(){destroyed=true;stopLoop();diagnostics.cleanups++;diagnostics.activeListenerSets=Math.max(0,diagnostics.activeListenerSets-1);window.removeEventListener("resize",resize);canvas.removeEventListener("pointerdown",pointerDown);canvas.removeEventListener("pointermove",pointerMove);canvas.removeEventListener("pointerup",pointerUp);canvas.removeEventListener("pointercancel",pointerUp);canvas.removeEventListener("wheel",wheel);if(controls)controls.removeEventListener("click",control);if(window.__OSB_GRAPH__===bridge)delete window.__OSB_GRAPH__};
    });
    return h2("div",{className:"graph-pane graph-pane-3d","data-graph-mode":"3d"},
      h2("canvas",{ref:canvasRef,id:"graphCanvas3D","aria-label":"Grafo tridimensional interativo"}),
      h2("div",{className:"graph-controls","data-graph-controls":true},
        h2("button",{className:"control-btn",title:"Aproximar câmera","data-tooltip":"Aproximar","aria-label":"Aproximar câmera","data-graph-control":"zoom-in"},"+"),
        h2("button",{className:"control-btn",title:"Afastar câmera","data-tooltip":"Afastar","aria-label":"Afastar câmera","data-graph-control":"zoom-out"},"−"),
        h2("button",{className:"control-btn",title:"Reenquadrar universo","data-tooltip":"Reenquadrar","aria-label":"Reenquadrar universo","data-graph-control":"fit-all"},uiIcon("frame",15)),
        h2("button",{className:"control-btn",title:"Focar nota selecionada","data-tooltip":"Focar seleção","aria-label":"Focar nota selecionada",disabled:!selected,"data-graph-control":"fit-selection"},uiIcon("target",15)),
        h2("button",{className:"control-btn orbit-control",title:reducedMotion?"Órbita automática desativada por movimento reduzido":"Alternar órbita automática","data-tooltip":"Órbita automática","aria-label":"Alternar órbita automática","aria-pressed":!reducedMotion,disabled:reducedMotion,"data-graph-control":"toggle-orbit"},"◎")
      ),
      h2("div",{className:"graph-dimension-badge"},reducedMotion?"3D · movimento reduzido":"3D · arraste para orbitar · scroll para zoom"),
      h2("div",{className:"graph-info"},h2("span",null,"NÓS: ",h2("span",{className:"val"},fmt(nodes.length))),h2("span",null,"LINKS: ",h2("span",{className:"val"},fmt(edges.length))),h2("span",null,selected?"▸ "+trunc(cleanLabel(selected.label),18):"Universo 3D"))
    );
  }

  /* ═══════════════════════════════════════════════════════════════
     BOTTOM PANEL — Active Memory / Timeline / Artifacts / Vault Notes
     ═══════════════════════════════════════════════════════════════ */
  function BottomPanel(props) {
    var snapshot=props.snapshot||{}, selected=props.selected, onNavigate=props.onNavigate;
    var tabS=hooks2.useState("timeline"), tab=tabS[0], setTab=tabS[1];
    var rangeS=hooks2.useState("7d"), range=rangeS[0], setRange=rangeS[1];
    var kindS=hooks2.useState("all"), eventKind=kindS[0], setEventKind=kindS[1];
    var activePreview=snapshot.active_preview||"";
    var events=asArray(snapshot.timeline_events||snapshot.recent_logs);
    var artifacts=asArray(snapshot.artifacts), vaultNotes=asArray(snapshot.vault_notes);
    var limits=snapshot.limits||{};
    var days=range==="today"?1:(range==="30d"?30:7), cutoff=Date.now()-days*86400000;
    var filteredEvents=events.filter(function(ev){
      if(eventKind!=="all"&&ev.kind!==eventKind&&ev.area!==eventKind)return false;
      if(!ev.timestamp)return true;
      var t=Date.parse(ev.timestamp);return !isNaN(t)&&t>=cutoff;
    });
    var tabs=[
      {key:"timeline",label:"Atividade",count:filteredEvents.length},
      {key:"artifacts",label:"Artefatos",count:artifacts.length},
      {key:"vault",label:"Vault Notes",count:vaultNotes.length},
      {key:"active",label:"Active Memory",count:activePreview?"★":0}
    ];
    function eventTime(ev){
      if(ev.timestamp){try{return new Date(ev.timestamp).toLocaleString(undefined,{day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit"})}catch(_e){}}
      return ev.time||"";
    }
    function cardNode(item){return props.nodeMap&&props.nodeMap[item.id]||item}
    return h2("section",{className:"bottom-panel","data-mobile-surface":"activity","data-timeline-range":range,"aria-label":"Atividade e coleções"},
      h2("div",{className:"bp-head"},
        h2("div",{className:"bp-tabs",role:"tablist"},tabs.map(function(t){return h2("button",{
          key:t.key,className:"bp-tab"+(tab===t.key?" active":""),role:"tab","aria-selected":tab===t.key,
          onClick:function(){setTab(t.key)}
        },t.label,t.count?h2("span",{className:"bp-count"},t.count):null)})),
        h2("button",{className:"pane-action pane-action-label","data-collapse-pane":"activity",title:"Ocultar o painel de atividade","aria-label":"Recolher atividade",onClick:props.onCollapse},uiIcon("down",13),h2("span",null,"Recolher"))
      ),
      tab==="timeline"?h2("div",{className:"timeline-filters"},
        h2("div",{"data-timeline-range":range,className:"filter-chips"},[
          ["today","Hoje"],["7d","7 dias"],["30d","30 dias"]
        ].map(function(x){return h2("button",{className:"chip"+(range===x[0]?" active":""),onClick:function(){setRange(x[0])}},x[1])})),
        h2("select",{className:"compact-select",value:eventKind,"aria-label":"Filtrar atividade",onChange:function(e){setEventKind(e.target.value)}},
          ["all","brain","projects","preference","signal","log","modified","created"].map(function(k){return h2("option",{value:k},k==="all"?"Todos os tipos":kindLabel(k))}))
      ):null,
      h2("div",{className:"bp-content"},
        tab==="timeline"?(filteredEvents.length?h2("div",{className:"timeline-list"},filteredEvents.map(function(ev,i){
          var node=ev.node_id&&props.nodeMap&&props.nodeMap[ev.node_id];
          return h2("button",{key:(ev.timestamp||ev.time||"")+i,className:"timeline-card kind-"+(ev.kind||"modified"),disabled:!node,
            "data-node-id":ev.node_id||"",onClick:function(){if(node)onNavigate(node)}},
            h2("span",{className:"event-mark","aria-hidden":"true"}),
            h2("span",{className:"event-body"},h2("span",{className:"bp-log-time"},eventTime(ev)),h2("strong",null,ev.kind||"modified"),h2("span",{className:"bp-log-text"},trunc(ev.text||ev.label||"Arquivo atualizado",140))),
            ev.area?h2("span",{className:"tag"},areaLabel(ev.area)):null)
        })):h2("div",{className:"bp-empty"},"Sem eventos reais neste período.")):
        tab==="artifacts"?(artifacts.length?h2("div",{className:"bp-grid"},artifacts.map(function(a,i){var n=cardNode(a);return h2("button",{
          key:a.id||i,className:"bp-art-card"+(selected&&selected.id===a.id?" selected":""),"data-node-card":"artifact","data-node-id":a.id||"",onClick:function(){onNavigate(n)}
        },h2("span",{className:"bp-art-kind"},h2("span",{className:"dot-"+(a.kind||"note")}),kindLabel(a.kind)),h2("span",{className:"bp-art-label"},trunc(cleanLabel(a.label),44)),a.topic?h2("span",{className:"bp-art-topic"},a.topic):null)})):h2("div",{className:"bp-empty"},"Nenhum artefato.")):
        tab==="vault"?(vaultNotes.length?h2("div",{className:"bp-grid"},vaultNotes.map(function(v,i){var n=cardNode(v);return h2("button",{
          key:v.id||i,className:"bp-vault-card"+(selected&&selected.id===v.id?" selected":""),"data-node-card":"vault","data-node-id":v.id||"","data-area":v.area||"other",onClick:function(){onNavigate(n)}
        },h2("span",{className:"bp-vault-area"},h2("span",{className:"dot-"+(v.area||"other")}),areaLabel(v.area)),h2("span",{className:"bp-vault-label"},trunc(cleanLabel(v.label),44)),h2("span",{className:"bp-vault-path"},trunc(v.id,58)))})):h2("div",{className:"bp-empty"},"Nenhuma nota no vault.")):
        activePreview?h2("pre",{className:"bp-pre"},activePreview):h2("div",{className:"bp-empty"},"Active Memory indisponível."),
        ((tab==="artifacts"&&limits.artifacts)||(tab==="vault"&&limits.vault_notes))?h2("div",{className:"dataset-cap"},
          (tab==="artifacts"?limits.artifacts.shown:limits.vault_notes.shown)+" de "+(tab==="artifacts"?limits.artifacts.total:limits.vault_notes.total)+
          ((tab==="artifacts"?limits.artifacts.truncated:limits.vault_notes.truncated)?" · limite do snapshot":"")):null
      )
    );
  }

  var SESSION_STATE={noteWidth:340,activityHeight:300,graphDimension:"2d",collapsed:{explorer:false,inspector:false,activity:false},savedViews:[]};
  function timeAgo(value){
    var t=Date.parse(value||"");if(isNaN(t))return "agora";
    var sec=Math.max(0,Math.floor((Date.now()-t)/1000));
    if(sec<60)return "há "+sec+"s";if(sec<3600)return "há "+Math.floor(sec/60)+"min";
    if(sec<86400)return "há "+Math.floor(sec/3600)+"h";return "há "+Math.floor(sec/86400)+"d";
  }
  function fuzzyScore(node,q){
    q=String(q||"").trim().toLowerCase();if(!q)return 1;
    var fields=[node.label,node.id,node.path,node.area,node.kind,node.status,node.topic].join(" ").toLowerCase();
    if(fields.indexOf(q)>=0)return 100-q.length;
    var pos=0,score=0;for(var i=0;i<fields.length&&pos<q.length;i++){if(fields[i]===q[pos]){score+=i===0||/\s|[-_/]/.test(fields[i-1])?3:1;pos++}}
    return pos===q.length?score:0;
  }
  function searchNorm(value){
    var text=String(value||"").toLowerCase();
    return typeof text.normalize==="function"?text.normalize("NFD").replace(/[\u0300-\u036f]/g,""):text;
  }
  function markdownNodes(text){
    var out=[],lines=String(text||"").split(/\r?\n/),list=[];
    function flush(){if(list.length){out.push(h2("ul",{className:"md-list"},list));list=[]}}
    lines.forEach(function(line,i){
      if(/^#{1,4}\s/.test(line)){flush();var level=Math.min(4,(line.match(/^#+/)||[""])[0].length);out.push(h2("h"+level,{key:"h"+i},line.replace(/^#{1,4}\s+/,"")))}
      else if(/^[-*]\s+/.test(line)){list.push(h2("li",{key:"l"+i},line.replace(/^[-*]\s+/,"")))}
      else if(/^>\s?/.test(line)){flush();out.push(h2("blockquote",{key:"q"+i},line.replace(/^>\s?/,"")))}
      else if(/^```/.test(line)){flush()}
      else if(!line.trim()){flush()}
      else{flush();out.push(h2("p",{key:"p"+i},line))}
    });flush();return out.length?out:h2("p",null,"Sem preview disponível.");
  }
  function deterministicCluster(nodes){
    nodes=asArray(nodes);if(!nodes.length)return "Resumo determinístico: nenhum nó no recorte atual.";
    var areas={},kinds={},tokens={};
    nodes.forEach(function(n){areas[n.area||"other"]=(areas[n.area||"other"]||0)+1;kinds[n.kind||"note"]=(kinds[n.kind||"note"]||0)+1;
      cleanLabel(n.label).toLowerCase().split(/[^\p{L}\p{N}]+/u).forEach(function(t){if(t.length>3&&!/^(para|como|with|from|this|that|brain|nota|notes)$/.test(t))tokens[t]=(tokens[t]||0)+1})});
    function top(obj,n){return Object.keys(obj).sort(function(a,b){return obj[b]-obj[a]||a.localeCompare(b)}).slice(0,n).map(function(k){return k+" ("+obj[k]+")"}).join(", ")||"nenhum"}
    return "Resumo determinístico: "+nodes.length+" nós. Áreas dominantes: "+top(areas,3)+". Tipos: "+top(kinds,3)+". Termos recorrentes: "+top(tokens,5)+". Baseado apenas em metadados e títulos do recorte.";
  }
  function beginSplitter(event,axis,startValue,onValue,min,max){
    event.preventDefault();var start=axis==="x"?event.clientX:event.clientY;
    function move(e){var delta=(axis==="x"?e.clientX:e.clientY)-start;onValue(Math.max(min,Math.min(max,startValue+delta)),true)}
    function up(e){var delta=(axis==="x"?e.clientX:e.clientY)-start;onValue(Math.max(min,Math.min(max,startValue+delta)),false);window.removeEventListener("pointermove",move);window.removeEventListener("pointerup",up)}
    window.addEventListener("pointermove",move);window.addEventListener("pointerup",up,{once:true});
  }

  /* ═══════════════════════════════════════════════════════════════
     SECOND BRAIN DASHBOARD — Full workspace
     ═══════════════════════════════════════════════════════════════ */
  function SecondBrainDashboardV3(){
    var snapS=hooks2.useState(null),snapshot=snapS[0],setSnapshot=snapS[1];
    var loadS=hooks2.useState(true),loading=loadS[0],setLoading=loadS[1];
    var errS=hooks2.useState(""),error=errS[0],setError=errS[1];
    var selS=hooks2.useState(null),selectedId=selS[0],setSelectedId=selS[1];
    var queryS=hooks2.useState(""),query=queryS[0],setQuery=queryS[1];
    var paletteS=hooks2.useState(false),paletteOpen=paletteS[0],setPaletteOpen=paletteS[1];
    var paletteIS=hooks2.useState(0),paletteIndex=paletteIS[0],setPaletteIndex=paletteIS[1];
    var layerS=hooks2.useState("all"),layerFilter=layerS[0],setLayerFilter=layerS[1];
    var areaS=hooks2.useState(null),activeArea=areaS[0],setActiveArea=areaS[1];
    var presetS=hooks2.useState("overview"),preset=presetS[0],setPreset=presetS[1];
    var focusS=hooks2.useState(0),focusDepth=focusS[0],setFocusDepth=focusS[1];
    var mobileS=hooks2.useState("graph"),mobileMode=mobileS[0],setMobileMode=mobileS[1];
    var expandedS=hooks2.useState({brain:true}),expanded=expandedS[0],setExpanded=expandedS[1];
    var shownS=hooks2.useState({brain:20}),shown=shownS[0],setShown=shownS[1];
    var sortS=hooks2.useState("name"),sortBy=sortS[0],setSortBy=sortS[1];
    var inspectorS=hooks2.useState("rendered"),inspectorMode=inspectorS[0],setInspectorMode=inspectorS[1];
    var copyS=hooks2.useState(""),copyStatus=copyS[0],setCopyStatus=copyS[1];
    var collapseS=hooks2.useState(Object.assign({},SESSION_STATE.collapsed)),collapsed=collapseS[0],setCollapsed=collapseS[1];
    var widthS=hooks2.useState(SESSION_STATE.noteWidth),noteWidth=widthS[0],setNoteWidth=widthS[1];
    var activityS=hooks2.useState(SESSION_STATE.activityHeight),activityHeight=activityS[0],setActivityHeight=activityS[1];
    var savedS=hooks2.useState(SESSION_STATE.savedViews.slice()),savedViews=savedS[0],setSavedViews=savedS[1];
    var viewNameS=hooks2.useState(""),viewName=viewNameS[0],setViewName=viewNameS[1];
    var clusterS=hooks2.useState(""),clusterSummary=clusterS[0],setClusterSummary=clusterS[1];
    var maxS=hooks2.useState(false),graphMax=maxS[0],setGraphMax=maxS[1];
    var viewsS=hooks2.useState(false),viewsOpen=viewsS[0],setViewsOpen=viewsS[1];
    var dimensionS=hooks2.useState(SESSION_STATE.graphDimension||"2d"),graphDimension=dimensionS[0],setGraphDimension=dimensionS[1];

    var refresh=hooks2.useCallback(function(){
      setLoading(true);setError("");
      fetchJSON(API).then(function(data){
        setSnapshot(data);
        var nodes=(data.graph&&data.graph.nodes)||[];
        if(!selectedId||!nodes.some(function(n){return n.id===selectedId})){
          var initial=nodes.find(function(n){return n.kind!=="active"&&n.kind!=="log"})||nodes[0];
          if(initial)setSelectedId(initial.id);
        }
      }).catch(function(e){setError(e&&e.message?e.message:String(e))}).finally(function(){setLoading(false)});
    },[]);
    hooks2.useEffect(function(){refresh()},[refresh]);

    var provider=snapshot&&snapshot.provider||{},counts=snapshot&&snapshot.counts||{},graph=snapshot&&snapshot.graph||{nodes:[],edges:[]};
    var capabilities=snapshot&&snapshot.capabilities||{graph_3d:true},graph3dEnabled=capabilities.graph_3d!==false;
    if(!graph3dEnabled&&graphDimension==="3d"){graphDimension="2d";SESSION_STATE.graphDimension="2d"}
    var allNodes=asArray(graph.nodes),allEdges=asArray(graph.edges),summary=snapshot&&snapshot.graph_summary||{};
    var nodeMap={};allNodes.forEach(function(n){nodeMap[n.id]=n});
    var selected=nodeMap[selectedId]||null;
    var brokenSources={};asArray(snapshot&&snapshot.broken_links).forEach(function(link){brokenSources[link.source]=true});
    var orphanIds={};asArray(summary.orphan_ids||snapshot&&snapshot.orphan_ids).forEach(function(id){orphanIds[id]=true});
    var hubIds={};asArray(summary.hub_ids||snapshot&&snapshot.hub_ids).forEach(function(id){hubIds[id]=true});

    function clearFilters(){setQuery("");setLayerFilter("all");setActiveArea(null);setPreset("all");setFocusDepth(0);setPaletteOpen(false)}
    function navigateToNode(node,options){
      if(!node||!node.id)return;
      options=options||{};setQuery("");setLayerFilter("all");setPreset("all");
      setActiveArea(options.keepArea?activeArea:(node.layer==="brain"?"brain":(node.area||null)));
      setSelectedId(node.id);setPaletteOpen(false);if(options.focus)setFocusDepth(options.focus);
      if(window.innerWidth<=768)setMobileMode("note");
    }
    window.__OSB_NAVIGATE__=navigateToNode;

    function presetPass(n){
      if(preset==="all"||preset==="overview")return preset!=="overview"||!((n.kind==="log")||(n.area==="templates"));
      if(preset==="brain")return n.layer==="brain";
      if(preset==="vault")return n.layer==="vault";
      if(preset==="projects")return n.area==="projects";
      if(preset==="recent"){var t=Date.parse(n.modified_at||"");return !isNaN(t)&&t>=Date.now()-30*86400000}
      if(preset==="hubs")return !!hubIds[n.id];
      if(preset==="orphans")return !!orphanIds[n.id];
      if(preset==="broken")return !!brokenSources[n.id];
      return true;
    }
    var normalizedQuery=searchNorm(query);
    var directMatches=normalizedQuery?allNodes.filter(function(n){return [n.label,n.id,n.topic,n.kind,n.area].some(function(value){return searchNorm(value).indexOf(normalizedQuery)>=0})}):[];
    var restrictToDirect=directMatches.length>0;
    var directIds={};directMatches.forEach(function(n){directIds[n.id]=true});
    var scored=allNodes.map(function(n){return{node:n,score:fuzzyScore(n,query)}});
    var baseNodes=scored.filter(function(x){var n=x.node;
      if(query&&((restrictToDirect&&!directIds[n.id])||(!restrictToDirect&&!x.score)))return false;if(!presetPass(n))return false;
      if(layerFilter!=="all"&&n.layer!==layerFilter&&n.kind!==layerFilter)return false;
      if(activeArea&&(n.layer==="brain"?activeArea!=="brain":(n.area||"other")!==activeArea))return false;
      return true;
    }).sort(function(a,b){return query?b.score-a.score:0}).map(function(x){return x.node});
    var baseIds={};baseNodes.forEach(function(n){baseIds[n.id]=true});

    function focusNeighborhood(depth){
      var ids={};if(!selected||!depth)return ids;
      ids[selected.id]=true;var frontier=[selected.id];
      for(var step=0;step<depth&&frontier.length;step++){
        var next=[];allEdges.forEach(function(e){
          if(frontier.indexOf(e.source)>=0&&!ids[e.target]){ids[e.target]=true;next.push(e.target)}
          if(frontier.indexOf(e.target)>=0&&!ids[e.source]){ids[e.source]=true;next.push(e.source)}
        });frontier=next;
      }
      return ids;
    }
    var hop1Ids=focusNeighborhood(1),hop2Ids=focusNeighborhood(2);
    var hop1Count=Object.keys(hop1Ids).length,hop2Count=Object.keys(hop2Ids).length;
    var hop2Outside=Object.keys(hop2Ids).filter(function(id){return !baseIds[id]}).length;
    var focusIds=focusDepth===1?hop1Ids:(focusDepth===2?hop2Ids:{});
    var graphNodes=focusDepth?Object.keys(focusIds).map(function(id){var n=nodeMap[id];if(!n)return null;return Object.assign({},n,{ghost:!baseIds[id]})}).filter(Boolean):baseNodes;
    var graphIds={};graphNodes.forEach(function(n){graphIds[n.id]=true});
    var graphEdges=allEdges.filter(function(e){return graphIds[e.source]&&graphIds[e.target]});
    var ghostCount=graphNodes.filter(function(n){return n.ghost}).length;

    var groups={};baseNodes.forEach(function(n){var g=n.layer==="brain"?"brain":(n.area||"other");(groups[g]||(groups[g]=[])).push(n)});
    var groupOrder=["brain","inbox","projects","clients","runbooks","decisions","references","templates","other"];
    function sorted(items){return items.slice().sort(function(a,b){if(sortBy==="modified")return String(b.modified_at||"").localeCompare(String(a.modified_at||""));if(sortBy==="area")return String(a.area||"").localeCompare(String(b.area||""));if(sortBy==="degree")return (b.degree||0)-(a.degree||0);return cleanLabel(a.label).localeCompare(cleanLabel(b.label))})}

    var backlinks=selected?allEdges.filter(function(e){return e.source===selected.id||e.target===selected.id}).map(function(e){var id=e.source===selected.id?e.target:e.source;return{node:nodeMap[id],kind:e.kind,outside:!baseIds[id]}}).filter(function(x){return x.node}):[];
    var searchResults=scored.filter(function(x){return query&&((restrictToDirect&&directIds[x.node.id])||(!restrictToDirect&&x.score))}).sort(function(a,b){return b.score-a.score||a.node.label.localeCompare(b.node.label)}).slice(0,10).map(function(x){return x.node});
    var commands=[
      {id:"clear",label:"Limpar filtros"},{id:"refresh",label:"Atualizar snapshot"},{id:"fit",label:"Ajustar grafo"},
      {id:"orphans",label:"Mostrar órfãos"},{id:"hubs",label:"Mostrar hubs"},{id:"dimension",label:"Alternar grafo 2D / 3D"},{id:"maximize",label:"Expandir grafo"},{id:"mobile",label:"Alternar modo mobile"}
    ];
    var paletteItems=query?searchResults.map(function(n){return{type:"node",node:n,label:n.label}}):commands.map(function(c){return{type:"command",command:c,label:c.label}});
    function changeGraphDimension(mode){if(mode==="3d"&&!graph3dEnabled)return;SESSION_STATE.graphDimension=mode;setGraphDimension(mode);if(window.innerWidth<=768)setMobileMode("graph")}
    function runCommand(id){if(id==="clear")clearFilters();else if(id==="refresh")refresh();else if(id==="fit"&&window.__OSB_GRAPH__)window.__OSB_GRAPH__.fitAll();else if(id==="orphans"){clearFilters();setPreset("orphans")}else if(id==="hubs"){clearFilters();setPreset("hubs")}else if(id==="dimension")changeGraphDimension(graphDimension==="2d"?"3d":"2d");else if(id==="maximize")setGraphMax(!graphMax);else if(id==="mobile")setMobileMode(mobileMode==="graph"?"vault":"graph");setPaletteOpen(false)}
    function activatePaletteItem(){var item=paletteItems[Math.min(paletteIndex,Math.max(0,paletteItems.length-1))];if(!item)return;if(item.type==="node")navigateToNode(item.node);else runCommand(item.command.id)}

    hooks2.useEffect(function(){
      function keydown(e){
        var target=e.target,typing=target&&(/INPUT|TEXTAREA|SELECT/.test(target.tagName)||target.isContentEditable);
        if((e.ctrlKey&&(e.code==="Space"||String(e.key).toLowerCase()==="k"))||(!typing&&e.key==="/")){e.preventDefault();setPaletteOpen(true);var input=document.querySelector(".osb-search-input");if(input)input.focus();return}
        if(e.key==="Escape"){if(query)setQuery("");setPaletteOpen(false);return}
        if(!paletteOpen)return;
        if(e.key==="ArrowDown"){e.preventDefault();setPaletteIndex((paletteIndex+1)%Math.max(1,paletteItems.length))}
        else if(e.key==="ArrowUp"){e.preventDefault();setPaletteIndex((paletteIndex-1+Math.max(1,paletteItems.length))%Math.max(1,paletteItems.length))}
        else if(e.key==="Enter"){e.preventDefault();activatePaletteItem()}
      }
      window.addEventListener("keydown",keydown);return function(){window.removeEventListener("keydown",keydown)};
    });

    function setPaneCollapsed(key){var next=Object.assign({},collapsed);next[key]=!next[key];SESSION_STATE.collapsed=next;setCollapsed(next)}
    function restoreLayout(){SESSION_STATE.noteWidth=340;SESSION_STATE.activityHeight=300;SESSION_STATE.collapsed={explorer:false,inspector:false,activity:false};setNoteWidth(340);setActivityHeight(300);setCollapsed(Object.assign({},SESSION_STATE.collapsed));setGraphMax(false)}
    function copyText(text,label){
      function done(){setCopyStatus(label+" copiado");setTimeout(function(){setCopyStatus("")},1600)}
      if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(text).then(done).catch(function(){setCopyStatus("Não foi possível copiar")});
      else{var ta=document.createElement("textarea");ta.value=text;document.body.appendChild(ta);ta.select();document.execCommand("copy");ta.remove();done()}
    }
    function applyPreset(id){clearFilters();setPreset(id);if(id==="brain")setActiveArea("brain");if(id==="projects")setActiveArea("projects")}
    function saveView(){var name=viewName.trim();if(!name)return;var view={id:"view-"+Date.now(),name:name,query:query,layer:layerFilter,area:activeArea,preset:preset,selected:selectedId,focus:focusDepth};var next=savedViews.concat([view]);SESSION_STATE.savedViews=next;setSavedViews(next);setViewName("")}
    function openView(v){setQuery(v.query||"");setLayerFilter(v.layer||"all");setActiveArea(v.area||null);setPreset(v.preset||"all");setSelectedId(v.selected||selectedId);setFocusDepth(v.focus||0)}
    function removeView(id){var next=savedViews.filter(function(v){return v.id!==id});SESSION_STATE.savedViews=next;setSavedViews(next)}
    function builtInView(id){if(id==="focus"){applyPreset("recent");setFocusDepth(selected?1:0)}else if(id==="projects")applyPreset("projects");else if(id==="memory"){applyPreset("recent");setActiveArea("brain")}else if(id==="runbooks"){clearFilters();setActiveArea("runbooks")}else applyPreset("orphans")}

    if(loading&&!snapshot)return h2("div",{className:"osb-app osb-loading-screen"},h2("div",{className:"osb-loader"}),h2("p",null,"Loading the read-only snapshot…"));
    if(error&&!snapshot)return h2("div",{className:"osb-app osb-loading-screen"},h2("p",{className:"osb-error-text"},error),h2("button",{className:"action-btn",onClick:refresh},"Tentar novamente"));

    var rootClass="osb-app mode-"+mobileMode+" dimension-"+graphDimension+(collapsed.explorer?" explorer-collapsed":"")+(collapsed.inspector?" inspector-collapsed":"")+(collapsed.activity?" activity-collapsed":"")+(graphMax?" graph-maximized":"");
    return h2("div",{className:rootClass,"data-all-count":allNodes.length,"data-visible-count":baseNodes.length,"data-selected-id":selectedId||"","data-active-area":activeArea||"","data-graph-dimension":graphDimension,style:{"--note-width":noteWidth+"px","--activity-height":activityHeight+"px"}},
      h2("header",null,
        h2("div",{className:"header-left"},h2("div",{className:"logo"},h2("span",{className:"logo-mark"},uiIcon("brain",16)),"OPEN_SECOND_BRAIN"),h2("button",{className:"top-icon",title:collapsed.explorer?"Mostrar explorador do vault":"Recolher explorador do vault","aria-label":collapsed.explorer?"Mostrar explorador":"Recolher explorador","aria-expanded":!collapsed.explorer,onClick:function(){setPaneCollapsed("explorer")}},uiIcon("panel",15))),
        h2("div",{className:"search-wrap"},
          h2("label",{className:"sr-only",for:"osb-search"},"Buscar notas e comandos"),
          h2("input",{id:"osb-search",type:"search",className:"search osb-search-input",placeholder:"Buscar ou comandar…  Ctrl+K",value:query,"aria-label":"Buscar notas e comandos","aria-expanded":paletteOpen,
            onFocus:function(){setPaletteOpen(true)},onChange:function(e){setQuery(e.target.value);setPaletteIndex(0);setPreset("all");setActiveArea(null)}}),
          h2("div",{className:"command-palette"+(paletteOpen?" open":""),"data-command-palette":true,"data-open":paletteOpen?"true":"false",role:"listbox"},
            paletteItems.length?h2("div",{"data-search-results":true},paletteItems.map(function(item,i){return h2("button",{className:"palette-item"+(i===paletteIndex?" active":""),role:"option","aria-selected":i===paletteIndex,"data-node-id":item.node&&item.node.id||"",onMouseDown:function(e){e.preventDefault()},onClick:function(){if(item.type==="node")navigateToNode(item.node);else runCommand(item.command.id)}},
              h2("span",{className:"palette-type"},item.type==="node"?areaLabel(item.node.area||"brain"):"COMANDO"),h2("span",null,cleanLabel(item.label))) })):h2("div",{className:"palette-empty"},"Nenhum resultado")
          )
        ),
        h2("div",{className:"header-right"},
          h2("button",{className:"freshness "+(error?"sync-off":"sync-on"),"data-action":"refresh",onClick:refresh,"aria-label":"Atualizar snapshot"},error?"● ERRO":"● ONLINE · "+timeAgo(snapshot&&snapshot.generated_at)),
          h2("span",{className:"ver"},"v3.1.0")
        )
      ),
      error&&snapshot?h2("div",{className:"stale-banner",role:"status"},"Falha ao atualizar. Mantendo o último snapshot válido. ",h2("button",{onClick:refresh},"Tentar novamente")):null,
      h2("nav",{className:"mobile-modes","aria-label":"Modo mobile"},[["vault","Vault"],["note","Nota"],["graph","Grafo"],["activity","Atividade"]].map(function(x){return h2("button",{"data-mobile-mode":x[0],className:mobileMode===x[0]?"active":"",onClick:function(){setMobileMode(x[0])}},x[1])})),
      h2("div",{className:"preset-bar","aria-label":"Presets do grafo"},[
        ["all","Tudo"],["brain","Brain"],["vault","Vault"],["projects","Projetos"],["recent","Recentes"],["hubs","Hubs"],["orphans","Órfãos"],["broken","Links quebrados"]
      ].map(function(x){return h2("button",{"data-preset":x[0],className:"preset-btn"+(preset===x[0]?" active":""),onClick:function(){applyPreset(x[0])}},x[1])}),
        activeArea?h2("button",{className:"active-area-chip","data-active-area":activeArea,onClick:function(){setActiveArea(null)},"aria-label":"Remover filtro de área"},areaLabel(activeArea)+" ×"):null,
        h2("button",{className:"preset-btn views-toggle","data-action":"toggle-views","aria-expanded":viewsOpen,onClick:function(){setViewsOpen(!viewsOpen)}},"Views"),
        h2("span",{"data-ghost-count":ghostCount,className:"ghost-count"},ghostCount?ghostCount+" relacionados fora do filtro":"sem ghost links")
      ),
      h2("div",{className:"main-area"},
        h2("aside",{className:"sidebar","data-mobile-surface":"vault","aria-label":"Explorador do vault"},
          h2("div",{className:"pane-head"},h2("span",null,"// VAULT"),h2("button",{className:"pane-action",title:"Recolher explorador do vault","data-collapse-pane":"explorer",onClick:function(){setPaneCollapsed("explorer")},"aria-label":"Recolher explorador"},uiIcon("left",13))),
          h2("div",{className:"sidebar-count","data-visible-count":true},baseNodes.length+" "+(baseNodes.length===1?"nota visível":"notas visíveis")),
          h2("label",{className:"sort-label"},"Ordenar",
            h2("select",{value:sortBy,onChange:function(e){setSortBy(e.target.value)}},
              [["name","Nome"],["modified","Modificada"],["area","Área"],["degree","Grau"]].map(function(x){
                return h2("option",{value:x[0]},x[1]);
              })
            )
          ),
          h2("div",{className:"explorer-progress explorer-summary","data-explorer-progress":"summary"},"Filtro: "+baseNodes.length+" de "+allNodes.length+" notas"),
          groupOrder.filter(function(g){return groups[g]&&groups[g].length}).map(function(g){var items=sorted(groups[g]),limit=shown[g]||20,isOpen=!!expanded[g];return h2("section",{className:"folder-group",key:g},
            h2("div",{className:"folder-row"},
              h2("button",{className:"folder-arrow"+(isOpen?" open":""),onClick:function(){var n=Object.assign({},expanded);n[g]=!n[g];setExpanded(n)},"aria-label":(isOpen?"Recolher ":"Expandir ")+areaLabel(g),"aria-expanded":isOpen},"▸"),
              h2("button",{className:"folder-label"+(activeArea===g?" active":""),"data-area-filter":g,onClick:function(){setActiveArea(activeArea===g?null:g);setPreset("all");var n=Object.assign({},expanded);n[g]=true;setExpanded(n)}},h2("span",{className:g==="brain"?"dot-brain":areaDotClass(g)}),h2("span",{className:"folder-name"},areaLabel(g)),h2("span",{className:"folder-count"},items.length))
            ),
            isOpen?h2("div",{className:"folder-children"},items.slice(0,limit).map(function(n){return h2("button",{className:"file-item child"+(selectedId===n.id?" active":""),"data-node-id":n.id,onClick:function(){navigateToNode(n,{keepArea:true})}},h2("span",{className:n.layer==="brain"?"dot-"+n.kind:areaDotClass(n.area)}),h2("span",null,trunc(cleanLabel(n.label),30)))}),
              h2("div",{className:"explorer-progress","data-explorer-progress":g},Math.min(limit,items.length)+" de "+items.length),
              limit<items.length?h2("button",{className:"show-more",onClick:function(){var next=Object.assign({},shown);next[g]=limit+28;setShown(next)}},"Mostrar mais"):null
            ):null
          )}),
          !baseNodes.length?h2("div",{className:"filter-empty"},h2("strong",null,"Nenhuma nota combina"),h2("p",null,"Os filtros atuais são incompatíveis."),h2("button",{onClick:clearFilters},"Limpar filtros")):null,
          h2("div",{className:"sidebar-section"},
            h2("div",{className:"sidebar-title"},"// Camadas"),
            h2("div",{className:"filter-chips"},["all","brain","vault","preference","signal"].map(function(f){
              return h2("button",{
                "data-layer-filter":f,
                className:"chip"+(layerFilter===f?" active":""),
                onClick:function(){
                  if(f==="all")clearFilters();
                  else{setLayerFilter(f);setPreset("all");setActiveArea(f==="brain"?"brain":null);}
                }
              },f==="all"?"Tudo":kindLabel(f));
            }))
          ),
          h2("div",{className:"stat-grid"},[[counts.preferences,"Prefs"],[counts.inbox,"Inbox"],[counts.logs,"Logs"],[counts.hermes_memory,"Mem"]].map(function(x){return h2("div",{className:"stat-item"},h2("span",{className:"stat-val"},fmt(x[0]||0)),h2("span",{className:"stat-lbl"},x[1]))}))
        ),
        h2("div",{className:"workspace"},
          h2("div",{className:"workspace-top"},
            h2("section",{className:"note-pane","data-mobile-surface":"note","aria-label":"Inspetor da nota"},
              h2("div",{className:"pane-head"},h2("span",null,"// INSPECTOR"),h2("button",{className:"pane-action",title:"Recolher inspetor da nota","data-collapse-pane":"inspector",onClick:function(){setPaneCollapsed("inspector")},"aria-label":"Recolher inspetor"},uiIcon("left",13))),
              selected?[
                h2("div",{className:"inspector-toolbar"},h2("div",{className:"segmented",role:"tablist"},[["rendered","Renderizado"],["source","Fonte"]].map(function(x){return h2("button",{"data-inspector-mode":x[0],className:inspectorMode===x[0]?"active":"",onClick:function(){setInspectorMode(x[0])}},x[1])})),
                  h2("button",{className:"icon-action",title:"Copiar caminho",onClick:function(){copyText(selected.id,"Caminho")},"aria-label":"Copiar caminho da nota"},uiIcon("copy",13)),h2("button",{className:"icon-action",title:"Copiar wikilink",onClick:function(){copyText("[["+selected.id.replace(/\.md$/,"")+"]]","Wikilink")},"aria-label":"Copiar wikilink"},uiIcon("link",13))),
                copyStatus?h2("div",{className:"copy-status",role:"status"},copyStatus):null,
                h2("h1",{className:"note-title"},cleanLabel(selected.label)),
                h2("div",{className:"properties-block"},[
                  ["tipo",kindLabel(selected.layer==="vault"?"vault":selected.kind)],["área",areaLabel(selected.area||"brain")],["id",selected.id],["modificada",selected.modified_at?new Date(selected.modified_at).toLocaleString():"—"],["tamanho",fmt(selected.size_bytes||0)+" B"],["grau",fmt(selected.degree||0)],["backlinks",fmt(selected.incoming||backlinks.length)]
                ].map(function(x){return h2("div",{className:"prop-row"},h2("span",{className:"prop-key"},x[0]+":"),h2("span",{className:x[0]==="id"?"prop-path":""},x[1]))})),
                h2("div",{className:"note-content"},inspectorMode==="source"?h2("pre",{className:"note-preview-text"},selected.preview||""):h2("div",{className:"markdown-rendered"},markdownNodes(selected.preview||""))),
                obsidianLink(provider,selected)?h2("a",{className:"obsidian-action",href:obsidianLink(provider,selected)},"Abrir no Obsidian"):null,
                h2("div",{className:"backlinks-section"},h2("div",{className:"backlinks-title"},"Backlinks ("+backlinks.length+")"),backlinks.length?backlinks.map(function(r){return h2("button",{className:"backlink-item",onClick:function(){navigateToNode(r.node)}},h2("span",{className:r.node.layer==="brain"?"dot-"+r.node.kind:areaDotClass(r.node.area)}),h2("span",{className:"backlink-label"},trunc(cleanLabel(r.node.label),34)),r.outside?h2("span",{className:"outside-badge"},"fora do filtro"):null)}):h2("p",{className:"no-backlinks"},"Sem backlinks."))
              ]:h2("div",{className:"empty-note"},"Selecione uma nota.")
            ),
            h2("div",{className:"splitter splitter-vertical",role:"separator","aria-label":"Redimensionar inspetor","aria-orientation":"vertical",tabIndex:0,onPointerDown:function(e){beginSplitter(e,"x",noteWidth,function(v,live){var app=document.querySelector(".osb-app");if(app)app.style.setProperty("--note-width",v+"px");if(!live){SESSION_STATE.noteWidth=v;setNoteWidth(v)}},260,520)}}),
            h2("section",{className:"graph-surface","data-mobile-surface":"graph"},
              h2("div",{className:"graph-toolbar"},
                h2("div",{className:"focus-switch","data-focus-depth":focusDepth,"aria-label":"Profundidade das conexões"},[
                  {d:0,label:"Visão geral",count:baseNodes.length,title:"Todas as notas do filtro atual"},
                  {d:1,label:"1 salto",count:hop1Count,title:"Nota selecionada e vizinhos diretos",disabled:!selected||hop1Count<=1},
                  {d:2,label:"2 saltos",count:hop2Count,title:"Vizinhos diretos e conexões dos vizinhos"+(hop2Outside?" · "+hop2Outside+" fora do filtro":""),disabled:!selected||hop2Count<=hop1Count}
                ].map(function(item){return h2("button",{className:focusDepth===item.d?"active":"",title:item.title,onClick:function(){setFocusDepth(item.d)},disabled:!!item.disabled,"data-focus-count":item.count,"data-focus-option":item.d},h2("span",null,item.label),h2("span",{className:"focus-count"},fmt(item.count)))})),
                h2("div",{className:"dimension-switch",role:"group","aria-label":"Dimensão do grafo"},
                  h2("button",{className:graphDimension==="2d"?"active":"","data-graph-dimension":"2d","aria-pressed":graphDimension==="2d",title:"Grafo 2D clássico",onClick:function(){changeGraphDimension("2d")}},"2D"),
                  graph3dEnabled?h2("button",{className:graphDimension==="3d"?"active":"","data-graph-dimension":"3d","aria-pressed":graphDimension==="3d",title:"Universo 3D orbital",onClick:function(){changeGraphDimension("3d")}},"3D"):null
                ),
                h2("div",{className:"toolbar-actions"},
                  h2("button",{className:"toolbar-btn",title:graphMax?"Voltar aos painéis":"Ocultar Vault, inspetor e atividade para ampliar o grafo","aria-label":graphMax?"Sair do modo expandido":"Expandir grafo","aria-pressed":graphMax,onClick:function(){setGraphMax(!graphMax)}},uiIcon("frame",13),h2("span",{className:"toolbar-label"},graphMax?"Sair do modo expandido":"Expandir grafo")),
                  !graphMax?h2("button",{className:"toolbar-btn",title:collapsed.activity?"Reabrir o painel de atividade":"Ocultar o painel de atividade","aria-label":collapsed.activity?"Mostrar atividade":"Ocultar atividade",onClick:function(){setPaneCollapsed("activity")}},uiIcon(collapsed.activity?"up":"down",13),h2("span",{className:"toolbar-label"},collapsed.activity?"Mostrar atividade":"Ocultar atividade")):null,
                  h2("button",{className:"toolbar-btn",title:"Reabrir painéis e voltar aos tamanhos padrão","aria-label":"Redefinir painéis",onClick:restoreLayout},uiIcon("reset",13),h2("span",{className:"toolbar-label"},"Redefinir painéis"))
                )
              ),
              graphDimension==="3d"?h2(GraphCanvas3D,{nodes:graphNodes,edges:graphEdges,selected:selected,onSelect:function(n){navigateToNode(nodeMap[n.id]||n,{focus:focusDepth})}}):h2(GraphCanvas,{nodes:graphNodes,edges:graphEdges,selected:selected,onSelect:function(n){navigateToNode(nodeMap[n.id]||n,{focus:focusDepth})}}),
              h2("div",{className:"graph-list","data-graph-list":true,"aria-label":"Lista equivalente do grafo"},graphNodes.slice(0,120).map(function(n){return h2("button",{"data-node-id":n.id,onClick:function(){navigateToNode(nodeMap[n.id]||n)}},n.label)}))
            )
          ),
          h2("div",{className:"splitter splitter-horizontal",role:"separator","aria-label":"Redimensionar atividade","aria-orientation":"horizontal",tabIndex:0,onPointerDown:function(e){beginSplitter(e,"y",activityHeight,function(v,live){var app=document.querySelector(".osb-app");if(app)app.style.setProperty("--activity-height",v+"px");if(!live){SESSION_STATE.activityHeight=v;setActivityHeight(v)}},220,430)}}),
          h2(BottomPanel,{snapshot:snapshot,selected:selected,nodeMap:nodeMap,onNavigate:navigateToNode,onCollapse:function(){setPaneCollapsed("activity")}})
        )
      ),
      h2("aside",{className:"views-drawer"+(viewsOpen?" open":""),"data-saved-views":true,"aria-hidden":viewsOpen?"false":"true"},
        h2("div",{className:"views-head"},h2("strong",null,"Views & inteligência"),h2("button",{"data-action":"explain-cluster",onClick:function(){setClusterSummary(deterministicCluster(graphNodes))}},"Explicar cluster")),
        h2("div",{className:"built-in-views"},[["focus","Meu foco atual"],["projects","Projetos ativos"],["memory","Memórias novas"],["runbooks","Runbooks relacionados"],["orphans","Notas sem conexão"]].map(function(x){return h2("button",{onClick:function(){builtInView(x[0])}},x[1])})),
        h2("div",{className:"save-view"},h2("input",{value:viewName,placeholder:"Nome da view",onChange:function(e){setViewName(e.target.value)},"aria-label":"Nome da view"}),h2("button",{onClick:saveView,disabled:!viewName.trim()},"Salvar")),
        savedViews.map(function(v){return h2("div",{className:"saved-view-row"},h2("button",{onClick:function(){openView(v)}},v.name),h2("button",{onClick:function(){removeView(v.id)},"aria-label":"Remover "+v.name},"×"))}),
        clusterSummary?h2("p",{className:"cluster-summary",role:"status"},clusterSummary):null,
        h2("small",null,"Resumo determinístico. Extensão futura: window.__OSB_CLUSTER_SUMMARIZER__; nenhuma API externa é chamada.")
      ),
      h2("div",{className:"status-bar"},h2("div",{className:"status-left"},h2("span",null,fmt(allNodes.length)+" notas"),h2("span",null,fmt(allEdges.length)+" links"),h2("span",null,fmt(baseNodes.length)+" visíveis"),h2("span",null,"rev "+trunc(snapshot&&snapshot.revision||"—",10))),h2("div",{className:"status-right"},h2("span",null,"read-only")))
    );
  }

  REG.register("hermes-osb-panel", SecondBrainDashboardV3);
})();

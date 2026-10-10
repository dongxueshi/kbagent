/* Local wiki-link rendering and graph interactions. Never execute document HTML. */
'use strict';
window.KBLinks = (() => {
  let graph = null;

  function destroyGraph() {
    if (graph) { graph.destroy(); graph = null; }
  }

  function renderMarkdown(text, target, links = []) {
    target.replaceChildren();
    let code = null;
    let fence = null;
    let offset = 0;
    const linksByStart = new Map(links.map(link => [link.start, link]));
    for (const line of text.split('\n')) {
      const chars = Array.from(line);
      const mark = line.match(/^ {0,3}(`{3,}|~{3,})/);
      if (code) {
        if (mark && mark[1][0] === fence[0] && mark[1].length >= fence.length && !line.slice(mark[0].length).trim()) {
          target.append(code); code = null; fence = null;
        } else { code.textContent += line + '\n'; }
      } else if (mark) {
        fence = mark[1]; code = document.createElement('pre');
      } else {
        const heading = line.match(/^(#{1,6})\s+(.*)/);
        const quote = line.startsWith('> ');
        const el = document.createElement(heading ? 'h' + heading[1].length : quote ? 'blockquote' : 'p');
        const prefix = heading ? line.length - heading[2].length : quote ? 2 : 0;
        if (/^([-*]|\d+\.)\s/.test(line)) el.classList.add('list-line');
        // API positions are Unicode code points, not JavaScript UTF-16 offsets.
        let plain = '';
        for (let i = prefix; i < chars.length;) {
          const link = linksByStart.get(offset + i);
          if (link && link.end <= offset + chars.length) {
            if (plain) { el.append(document.createTextNode(plain)); plain = ''; }
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'wiki-link ' + link.status;
            button.dataset.wikiLink = String(link.id);
            button.textContent = link.label || link.target_name || link.anchor;
            button.title = link.status === 'resolved' ? '打开 ' + link.target_title + (link.anchor ? ' · ' + link.anchor : '')
              : link.status === 'broken' ? '目标已删除，点击修复链接'
              : link.status === 'ambiguous' ? '存在同名文档，点击选择'
              : '文档尚未创建，点击创建';
            el.append(button);
            i = link.end - offset;
          } else { plain += chars[i++]; }
        }
        if (plain) el.append(document.createTextNode(plain));
        target.append(el);
      }
      offset += chars.length + 1;
    }
    if (code) target.append(code);
  }

  function scrollToAnchor(target, anchor) {
    const normalize = s => s.trim().toLocaleLowerCase().replace(/\s+/g, '-');
    const element = [...target.querySelectorAll('h1,h2,h3,h4,h5,h6')].find(el => normalize(el.textContent) === normalize(anchor));
    if (element) { element.scrollIntoView({block:'start', behavior:'smooth'}); return true; }
    return false;
  }

  function mountGraph(container, data, onOpen, onSelect) {
    destroyGraph();
    if (!window.cytoscape) throw new Error('图谱资源未加载，请刷新页面');
    graph = window.cytoscape({
      container,
      elements: [
        ...data.nodes.map(node => ({data:{id:node.id, label:node.title, depth:node.depth}, classes:node.id === data.center_id ? 'center' : ''})),
        ...data.edges.map((edge, i) => ({data:{id:'edge-' + i, source:edge.source, target:edge.target}})),
      ],
      style: [
        {selector:'node', style:{'background-color':'#b7a2e5', 'border-width':3, 'border-color':'#f0eafa', width:28, height:28, label:'data(label)', 'font-size':11, color:'#726882', 'text-valign':'bottom', 'text-margin-y':10, 'text-wrap':'ellipsis', 'text-max-width':115}},
        {selector:'node.center', style:{'background-color':'#7257d8', width:40, height:40, 'font-weight':'bold', color:'#7257d8'}},
        {selector:'node:selected', style:{'border-width':5, 'border-color':'#a38dd5'}},
        {selector:'edge', style:{width:1.3, 'line-color':'#ded6ed', 'target-arrow-color':'#c9b9e4', 'target-arrow-shape':'triangle', 'curve-style':'bezier', 'arrow-scale':0.7}},
      ],
      layout:{name:'concentric', concentric:node => 2 - node.data('depth'), levelWidth:() => 1, minNodeSpacing:45, animate:false, padding:40},
      minZoom:0.25, maxZoom:3, wheelSensitivity:0.25,
    });
    graph.on('tap', 'node', event => {
      const node = event.target;
      graph.nodes().unselect(); node.select();
      onSelect?.(node.id(), node.data('label'));
    });
    graph.on('dbltap', 'node', event => onOpen(event.target.id()));
    return graph;
  }

  function zoom(amount) {
    if (graph) graph.zoom({level:Math.max(0.25, Math.min(3, graph.zoom() * amount)), renderedPosition:{x:graph.width() / 2, y:graph.height() / 2}});
  }
  function fit() { graph?.fit(undefined, 40); }
  function filter(query) {
    if (!graph) return;
    const term = query.trim().toLocaleLowerCase();
    graph.nodes().style('opacity', node => !term || node.data('label').toLocaleLowerCase().includes(term) ? 1 : 0.15);
  }

  function attachAutocomplete(input, container, documents) {
    let options = [];
    let selected = 0;
    let start = 0;
    let queryEnd = 0;
    let dismissed = null;
    const close = () => { container.hidden = true; options = []; input.removeAttribute('aria-activedescendant'); input.setAttribute('aria-expanded', 'false'); };
    input.setAttribute('aria-controls', container.id);
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    container.setAttribute('role', 'listbox');

    function choose(index) {
      const option = options[index];
      if (!option) return;
      const doc = option.doc;
      const duplicates = documents.filter(d => d.title === doc.title || d.aliases?.includes(doc.title)).length > 1;
      const specialName = /[\[\]#|\n]/.test(doc.title) || doc.title.startsWith('id:');
      const label = doc.title.replaceAll('[', '［').replaceAll(']', '］').replaceAll('\n', ' ');
      const replacement = duplicates || specialName ? `[[id:${doc.id}|${label}]]` : `[[${doc.title}]]`;
      // Replace the active opener/query only; retain surrounding text and avoid duplicate closers.
      const end = input.value.slice(queryEnd, queryEnd + 2) === ']]' ? queryEnd + 2 : queryEnd;
      input.setRangeText(replacement, start, end, 'end');
      close(); input.focus(); input.dispatchEvent(new Event('input', {bubbles:true}));
    }

    function highlight() {
      [...container.children].forEach((el, i) => { el.classList.toggle('active', i === selected); el.setAttribute('aria-selected', String(i === selected)); });
      if (options.length) input.setAttribute('aria-activedescendant', 'wiki-option-' + selected);
    }

    function update() {
      const before = input.value.slice(0, input.selectionStart);
      const match = before.match(/\[\[([^\[\]\n#|]*)$/);
      if (!match || input.selectionStart !== input.selectionEnd) { close(); return; }
      start = before.length - match[0].length;
      queryEnd = input.selectionStart;
      if (dismissed === start + ':' + queryEnd) return;
      const query = match[1].trim().toLocaleLowerCase();
      options = documents.filter(doc => [doc.title, ...(doc.aliases || [])].some(name => name.toLocaleLowerCase().includes(query))).slice(0, 8).map(doc => ({doc}));
      if (!options.length) { close(); return; }
      container.replaceChildren(); selected = 0;
      for (let i = 0; i < options.length; i++) {
        const option = document.createElement('button');
        option.type = 'button'; option.id = 'wiki-option-' + i;
        option.setAttribute('role', 'option'); option.textContent = options[i].doc.title;
        const duplicate = documents.filter(d => d.title === options[i].doc.title).length > 1;
        if (duplicate) option.textContent += ' · ' + options[i].doc.id.slice(0, 6);
        option.addEventListener('mousedown', event => event.preventDefault());
        option.addEventListener('click', () => choose(i));
        container.append(option);
      }
      container.hidden = false; input.setAttribute('aria-expanded', 'true'); highlight();
    }

    input.addEventListener('input', () => { dismissed = null; update(); });
    input.addEventListener('click', update);
    input.addEventListener('keyup', event => { if (!['Enter', 'Escape', 'ArrowUp', 'ArrowDown', 'Tab'].includes(event.key)) update(); });
    input.addEventListener('blur', close);
    input.addEventListener('keydown', event => {
      if (container.hidden || event.isComposing) return;
      if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
        event.preventDefault(); selected = (selected + (event.key === 'ArrowDown' ? 1 : options.length - 1)) % options.length; highlight();
      } else if (event.key === 'Enter' || event.key === 'Tab') { event.preventDefault(); choose(selected); }
      else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); dismissed = start + ':' + queryEnd; close(); }
    });
  }

  return {renderMarkdown, scrollToAnchor, mountGraph, destroyGraph, zoom, fit, filter, attachAutocomplete};
})();

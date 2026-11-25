/* Orthogonal connectors for reported links between visible map cards. */
(function (root) {
 'use strict';

 const clearance = 4;
 const stub = 9;
 const binSize = 80;

 function indexCards(stage) {
  const cards = new Map(), columns = new Map(), rows = new Map();
  let maxHeight = 0, maxWidth = 0;
  function insert(bins, min, max, card) {
   for (let n = Math.floor(min / binSize); n <= Math.floor(max / binSize); n++) {
    if (!bins.has(n)) bins.set(n, []);
    bins.get(n).push(card);
   }
  }
  for (const [id, p] of Object.entries(stage.positions)) {
   const size = stage.sizes[id];
   if (!size) continue;
   const card = {left: p.x, top: p.y, right: p.x + size.width, bottom: p.y + size.height};
   cards.set(id, card);
   insert(columns, card.left - clearance, card.right + clearance, card);
   insert(rows, card.top - clearance, card.bottom + clearance, card);
   maxHeight = Math.max(maxHeight, size.height);
   maxWidth = Math.max(maxWidth, size.width);
  }
  for (const items of rows.values()) items.sort((a, b) => a.left - b.left);
  for (const items of columns.values()) items.sort((a, b) => a.top - b.top);

  // Limit each collision query to the cards along this particular line segment.
  function blocked(a, b) {
   const vertical = a.x === b.x;
   const line = vertical ? a.x : a.y;
   const items = (vertical ? columns : rows).get(Math.floor(line / binSize)) || [];
   const start = vertical ? 'top' : 'left', end = vertical ? 'bottom' : 'right';
   const crossStart = vertical ? 'left' : 'top', crossEnd = vertical ? 'right' : 'bottom';
   const min = Math.min(vertical ? a.y : a.x, vertical ? b.y : b.x) - clearance;
   const max = Math.max(vertical ? a.y : a.x, vertical ? b.y : b.x) + clearance;
   const cutoff = min - (vertical ? maxHeight : maxWidth);
   let lo = 0, hi = items.length;
   while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (items[mid][start] < cutoff) lo = mid + 1;
    else hi = mid;
   }
   for (let n = lo; n < items.length && items[n][start] < max; n++) {
    const card = items[n];
    if (card[end] > min && line > card[crossStart] - clearance && line < card[crossEnd] + clearance) return true;
   }
   return false;
  }
  return {cards, blocked};
 }

 function port(card, side, offset) {
  const x = (card.left + card.right) / 2 + offset, y = (card.top + card.bottom) / 2 + offset;
  switch (side) {
   case 'top': return [{x, y: card.top}, {x, y: card.top - stub}];
   case 'bottom': return [{x, y: card.bottom}, {x, y: card.bottom + stub}];
   case 'left': return [{x: card.left, y}, {x: card.left - stub, y}];
   default: return [{x: card.right, y}, {x: card.right + stub, y}];
  }
 }

 function svgPath(points) {
  const clean = [];
  for (const p of points) {
   const last = clean[clean.length - 1], prev = clean[clean.length - 2];
   if (last && p.x === last.x && p.y === last.y) continue;
   if (prev && ((p.x === last.x && p.x === prev.x) || (p.y === last.y && p.y === prev.y))) clean.pop();
   clean.push(p);
  }
  let path = 'M ' + clean[0].x + ' ' + clean[0].y;
  for (let i = 1; i < clean.length; i++) {
   path += clean[i].x === clean[i-1].x ? ' V ' + clean[i].y : ' H ' + clean[i].x;
  }
  return path;
 }

 function route(from, to, stage, edge, groups, blocked) {
  const down = from.top < to.top, right = from.left < to.left;
  const vertical = down ? ['bottom', 'top'] : ['top', 'bottom'];
  const horizontal = right ? ['right', 'left'] : ['left', 'right'];
  const onDifferentRows = from.bottom <= to.top || to.bottom <= from.top;
  const pairs = onDifferentRows ? [vertical, horizontal] : [horizontal, vertical];
  pairs.push(['left', 'left'], ['right', 'right'], ['top', 'top'], ['bottom', 'bottom']);
  const firstGroup = groups.get(stage.deviceGroups?.[edge.source]);
  const lastGroup = groups.get(stage.deviceGroups?.[edge.target]);
  // Small fixed offsets keep reported and manually annotated links individually visible.
  const offset = edge.origin === 'manual' ? 5 : edge.origin === 'zigbee' ? -4 : 0;
  let fallback;
  for (const [first, last] of pairs) {
   const [start, a] = port(from, first, offset);
   const [finish, b] = port(to, last, offset);
   function clear(via) {
    const points = [a, ...via, b];
    for (let i = 1; i < points.length; i++) {
     const p = points[i - 1], q = points[i];
     if (p.x !== q.x || p.y !== q.y) {
      if (blocked(p, q)) return null;
     }
    }
    return svgPath([start, ...points, finish]);
   }
   if (a.x === b.x || a.y === b.y) {
    const straight = clear([]);
    if (straight) return straight;
   }
   const midX = (a.x + b.x)/2, midY = (a.y + b.y)/2;
   const across = y => [{x:a.x, y}, {x:b.x, y}];
   const along = x => [{x, y:a.y}, {x, y:b.y}];
   const middle = onDifferentRows ? across(midY) : along(midX);
   if (!fallback) fallback = svgPath([start, a, ...middle, b, finish]);
   for (const via of [middle, [{x:a.x,y:b.y}], [{x:b.x,y:a.y}]]) {
    const path = clear(via);
    if (path) return path;
   }

   // Gaps beside cards and at group boundaries remain usable across multiple grid rows.
   const xLanes = [from.left-stub, from.right+stub, to.left-stub, to.right+stub];
   const yLanes = [from.top-stub, from.bottom+stub, to.top-stub, to.bottom+stub];
   for (const group of [firstGroup, lastGroup]) {
    if (!group) continue;
    xLanes.push(group.x+7, group.x+group.width-7);
    yLanes.push(group.y-7, group.y+group.height+7);
   }
   xLanes.push(5, stage.width-5);
   yLanes.push(5, stage.height-5);
   for (const x of new Set(xLanes)) {
    const path = clear(along(x));
    if (path) return path;
   }
   for (const y of new Set(yLanes)) {
    const path = clear(across(y));
    if (path) return path;
   }
  }
  // Manually overlapping or enclosing cards can leave every available corridor obstructed.
  return fallback;
 }

 function routes(stage, edges) {
  const {cards, blocked} = indexCards(stage);
  const groups = new Map(stage.groups.map(group => [group.network, group]));
  const rendered = new Set(), result = [];
  for (const edge of edges) {
   const from = cards.get(edge.source), to = cards.get(edge.target);
   if (!from || !to || from === to) continue;
   const key = JSON.stringify([edge.origin, ...[edge.source, edge.target].sort()]);
   if (rendered.has(key)) continue;
   rendered.add(key);
   result.push({edge, path: route(from, to, stage, edge, groups, blocked)});
  }
  return result;
 }
 if (typeof module !== 'undefined' && module.exports) module.exports = {routes};
 else root.RunvardMapEdges = {routes};
})(globalThis);

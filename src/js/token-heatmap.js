// GitHub-style calendar of my daily LLM token usage.
// src/data/token-usage.json is written by scripts/token_usage.py, which also
// renders the static SVG for the GitHub profile; keep the layout and levels in sync.
(() => {
    const WEEKS = 53, CELL = 10, GAP = 3, PITCH = CELL + GAP;
    const LEFT = 30, TOP = 17;
    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const SVG_NS = 'http://www.w3.org/2000/svg';

    const pad = n => String(n).padStart(2, '0');
    const isoDay = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
    const longDate = d => `${MONTHS[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
    const sum = values => values.reduce((a, b) => a + b, 0);

    // 752400000 -> "752M"
    function compact(n) {
        for (const [size, unit] of [[1e9, 'B'], [1e6, 'M'], [1e3, 'K']]) {
            const value = Number((n / size).toPrecision(3));
            if (value >= 1) return value + unit;
        }
        return String(n);
    }

    // Like GitHub, split the active days into four equal-sized levels.
    function quartiles(totals) {
        const active = totals.filter(t => t > 0).sort((a, b) => a - b);
        return active.length ? [0.25, 0.5, 0.75].map(q => active[Math.floor(q * (active.length - 1))]) : [];
    }

    const level = (total, cuts) => total <= 0 ? 0 : 1 + cuts.filter(cut => total > cut).length;

    function monthLabels(start) {
        const labels = [];
        for (let week = 0; week < WEEKS; week++) {
            const first = addDays(start, week * 7);
            if (week === 0 || first.getMonth() !== addDays(first, -7).getMonth()) {
                labels.push([week, MONTHS[first.getMonth()]]);
            }
        }
        // Drop labels that would collide: a partial first month, or one in the last column.
        if (labels.length > 1 && labels[1][0] - labels[0][0] < 2) labels.shift();
        return labels.filter(([week]) => week < WEEKS - 1);
    }

    function el(name, props = {}, children = []) {
        const node = Object.assign(document.createElement(name), props);
        node.append(...children);
        return node;
    }

    function svgEl(name, attrs, parent, text) {
        const node = document.createElementNS(SVG_NS, name);
        for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
        if (text) node.textContent = text;
        parent.appendChild(node);
        return node;
    }

    async function render(root) {
        let data;
        try {
            const response = await fetch(root.dataset.src);
            if (!response.ok) throw new Error(response.statusText);
            data = await response.json();
        } catch (err) {
            root.textContent = 'Token usage data is unavailable right now.';
            return;
        }

        const [year, month, date] = data.updated.split('-').map(Number);
        const end = new Date(year, month - 1, date);
        const start = addDays(end, -end.getDay() - (WEEKS - 1) * 7);
        const days = [];
        for (let day = start; day <= end; day = addDays(day, 1)) {
            const bySource = Object.entries(data.days[isoDay(day)] || {})
                .map(([source, counts]) => [data.sources[source] || source, sum(counts)])
                .filter(([, total]) => total > 0)
                .sort((a, b) => b[1] - a[1]);
            days.push({ day, bySource, total: sum(bySource.map(([, total]) => total)) });
        }
        const cuts = quartiles(days.map(d => d.total));
        const total = sum(days.map(d => d.total));

        const svg = document.createElementNS(SVG_NS, 'svg');
        svg.setAttribute('viewBox', `0 0 ${LEFT + WEEKS * PITCH - GAP} ${TOP + 7 * PITCH - GAP}`);
        svg.setAttribute('role', 'img');
        svg.setAttribute('aria-label', `Heatmap of daily token usage: ${compact(total)} tokens in the last year`);
        for (const [week, name] of monthLabels(start)) {
            svgEl('text', { x: LEFT + week * PITCH, y: TOP - 6 }, svg, name);
        }
        for (const [row, name] of [[1, 'Mon'], [3, 'Wed'], [5, 'Fri']]) {
            svgEl('text', { x: 0, y: TOP + row * PITCH + 9 }, svg, name);
        }
        days.forEach((d, i) => {
            const cell = svgEl('rect', {
                x: LEFT + Math.floor(i / 7) * PITCH + 0.5, y: TOP + (i % 7) * PITCH + 0.5,
                width: CELL - 1, height: CELL - 1, rx: 2, class: `th-l${level(d.total, cuts)}`,
            }, svg);
            cell.dataset.index = i;
        });

        const scroller = el('div', { className: 'th-scroll' }, [svg]);
        const tip = el('div', { className: 'th-tip', hidden: true });
        const legend = el('span', { className: 'th-legend' },
            ['Less', ...[0, 1, 2, 3, 4].map(l => el('i', { className: `th-l${l}` })), 'More']);
        root.replaceChildren(
            el('div', { className: 'th-head' },
                total ? [el('strong', { textContent: compact(total) }), ' tokens in the last year'] : ['No tokens in the last year']),
            scroller,
            el('div', { className: 'th-foot' }, [el('span', { textContent: `Updated ${longDate(end)}` }), legend]),
            tip,
        );
        scroller.scrollLeft = scroller.scrollWidth;  // on narrow screens, start at the latest weeks

        const hide = () => { tip.hidden = true; };
        function show(cell) {
            const d = days[cell.dataset.index];
            tip.replaceChildren(el('strong', { textContent: `${d.total ? compact(d.total) : 'No'} tokens on ${longDate(d.day)}` }));
            if (d.bySource.length) {
                tip.append(el('span', { textContent: d.bySource.map(([name, n]) => `${name} ${compact(n)}`).join(' · ') }));
            }
            tip.hidden = false;
            // Center the tip above the cell, but keep it inside the component.
            const box = cell.getBoundingClientRect(), host = root.getBoundingClientRect();
            const center = box.left + box.width / 2 - host.left, half = tip.offsetWidth / 2;
            const left = Math.min(Math.max(center, half), host.width - half);
            tip.style.left = `${left}px`;
            tip.style.top = `${box.top - host.top}px`;
            tip.style.setProperty('--arrow', `${center - left}px`);
        }
        svg.addEventListener('pointerover', e => (e.target.dataset && e.target.dataset.index ? show(e.target) : hide()));
        svg.addEventListener('pointerleave', e => { if (e.pointerType === 'mouse') hide(); });
        scroller.addEventListener('scroll', hide, { passive: true });
        document.addEventListener('pointerdown', e => { if (!root.contains(e.target)) hide(); });
    }

    document.querySelectorAll('.token-heatmap').forEach(render);
})();

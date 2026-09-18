/* 개인 페이지: 맵별 킬/데스 지점 히트맵.
   서버는 원시 게임 좌표만 주고, 미니맵 비율 변환과 지점 이름(콜아웃)은 여기서 한다.

   변환식 (실측 검증: 어센트 콜아웃 22/22 정위치)
       px = y * xm + xs        ← 게임 y 가 이미지 x
       py = x * ym + ys
   결과는 0~1 비율이라 %로 찍으면 이미지 크기와 무관하게 따라간다(반응형 무료). */
(function () {
    var dataEl = document.getElementById('heat-data');
    var panel = document.getElementById('heat-panel');
    if (!dataEl || !panel) return;

    var heat = JSON.parse(dataEl.textContent);
    if (!heat.length) return;

    var img = panel.querySelector('[data-role="heat-img"]');
    var dots = panel.querySelector('[data-role="heat-dots"]');
    var rows = panel.querySelector('[data-role="heat-rows"]');
    var msg = panel.querySelector('[data-role="heat-msg"]');
    var maps = null;
    var current = heat[0];
    var mode = 'both';
    var rays = true;

    function show(text) { msg.textContent = text; msg.hidden = !text; }

    function toPct(meta, x, y) {
        return [(y * meta.xm + meta.xs) * 100, (x * meta.ym + meta.ys) * 100];
    }

    /* 미니맵은 모두 정사각(1024x1024)이라 % 단위 delta 로 화면 각도를 그대로 낼 수 있다. */
    function degTo(meta, fromX, fromY, toX, toY) {
        var a = toPct(meta, fromX, fromY), b = toPct(meta, toX, toY);
        return Math.atan2(b[1] - a[1], b[0] - a[0]) * 180 / Math.PI;
    }

    /** 게임 좌표계 시선각(라디안) → 화면 각도(도).
        게임 방향 (cos v, sin v) 를 미니맵 축으로 옮기면 (sin v * xm, cos v * ym).
        맵마다 xm 부호가 달라 계수를 그대로 태워야 한다. */
    function aimDeg(meta, view) {
        return Math.atan2(Math.cos(view) * meta.ym, Math.sin(view) * meta.xm) * 180 / Math.PI;
    }

    function ray(cls, xy, deg) {
        var r = document.createElement('span');
        r.className = 'heat-ray ' + cls;
        r.style.left = xy[0] + '%';
        r.style.top = xy[1] + '%';
        r.style.transform = 'rotate(' + deg.toFixed(1) + 'deg)';
        return r;
    }

    /** 게임 좌표에서 가장 가까운 콜아웃 이름. 높이(z)는 무시한다. */
    function nearestCallout(meta, x, y) {
        var best = null, bestD = Infinity;
        for (var i = 0; i < meta.callouts.length; i++) {
            var c = meta.callouts[i];
            var d = (c.x - x) * (c.x - x) + (c.y - y) * (c.y - y);
            if (d < bestD) { bestD = d; best = c; }
        }
        if (!best) return null;
        return (best.region ? best.region + ' ' : '') + best.name;
    }

    function render() {
        var meta = maps[current.uuid];
        if (!meta) {
            show('이 맵의 미니맵 정보를 찾지 못했어요 (uv run python -m app.tools.sync_maps 로 갱신).');
            dots.innerHTML = '';
            rows.innerHTML = '';
            return;
        }
        show('');
        img.src = meta.icon;
        img.alt = meta.name + ' 미니맵';

        var spots = {};  // 지점 이름 → {kills, deaths}
        dots.innerHTML = '';
        var frag = document.createDocumentFragment();

        function place(points, cls, key) {
            points.forEach(function (p) {
                var name = nearestCallout(meta, p[0], p[1]);
                if (name) {
                    var s = spots[name] || (spots[name] = { kills: 0, deaths: 0 });
                    s[key] += 1;
                }
                if (mode !== 'both' && mode !== key) return;
                var xy = toPct(meta, p[0], p[1]);
                if (xy[0] < 0 || xy[0] > 100 || xy[1] < 0 || xy[1] > 100) return;

                // 방향: 킬은 내 조준각(없으면 상대 쪽), 데스는 나를 쏜 놈 쪽.
                var deg = null, other = null;
                if (key === 'kills') {
                    if (p[2] !== null && p[2] !== undefined) deg = aimDeg(meta, p[2]);
                    else if (p[3] !== null && p[3] !== undefined) deg = degTo(meta, p[0], p[1], p[3], p[4]);
                    if (p[3] !== null && p[3] !== undefined) other = nearestCallout(meta, p[3], p[4]);
                } else if (p[2] !== null && p[2] !== undefined) {
                    deg = degTo(meta, p[0], p[1], p[2], p[3]);
                    other = nearestCallout(meta, p[2], p[3]);
                }
                if (rays && deg !== null) frag.appendChild(ray('ray-' + key, xy, deg));

                var d = document.createElement('span');
                d.className = 'heat-dot ' + cls;
                d.style.left = xy[0] + '%';
                d.style.top = xy[1] + '%';
                d.title = (key === 'kills' ? '킬' : '데스') + (name ? ' · ' + name : '') +
                    (other ? (key === 'kills' ? ' → 상대 ' + other : ' ← ' + other + ' 쪽에서 피격') : '');
                frag.appendChild(d);
            });
        }
        place(current.kills, 'dot-kill', 'kills');
        place(current.deaths, 'dot-death', 'deaths');
        dots.appendChild(frag);

        var list = Object.keys(spots).map(function (name) {
            return { name: name, kills: spots[name].kills, deaths: spots[name].deaths };
        });
        var topKill = list.slice().sort(function (a, b) { return b.kills - a.kills; })[0];
        var topDeath = list.slice().sort(function (a, b) { return b.deaths - a.deaths; })[0];
        panel.querySelector('[data-role="top-kill"]').textContent =
            (topKill && topKill.kills) ? topKill.name + ' (' + topKill.kills + '킬)' : '-';
        panel.querySelector('[data-role="top-death"]').textContent =
            (topDeath && topDeath.deaths) ? topDeath.name + ' (' + topDeath.deaths + '데스)' : '-';
        panel.querySelector('[data-role="heat-count"]').textContent =
            current.kills.length + ' / ' + current.deaths.length;

        list.sort(function (a, b) { return (b.kills + b.deaths) - (a.kills + a.deaths); });
        rows.innerHTML = '';
        list.slice(0, 8).forEach(function (s) {
            var tr = document.createElement('tr');
            tr.innerHTML = '<td>' + s.name + '</td><td class="num">' + s.kills +
                '</td><td class="num">' + s.deaths + '</td>';
            rows.appendChild(tr);
        });
    }

    panel.querySelectorAll('[data-role="heat-maps"] .btn-tab').forEach(function (b) {
        b.addEventListener('click', function () {
            panel.querySelectorAll('[data-role="heat-maps"] .btn-tab')
                .forEach(function (x) { x.classList.remove('active'); });
            b.classList.add('active');
            current = heat.filter(function (h) { return h.uuid === b.dataset.uuid; })[0];
            render();
        });
    });
    var rayToggle = panel.querySelector('[data-role="heat-rays"]');
    if (rayToggle) {
        rayToggle.addEventListener('change', function () {
            rays = rayToggle.checked;
            if (maps) render();
        });
    }
    panel.querySelectorAll('[data-role="heat-mode"]').forEach(function (b) {
        b.addEventListener('click', function () {
            panel.querySelectorAll('[data-role="heat-mode"]')
                .forEach(function (x) { x.classList.remove('active'); });
            b.classList.add('active');
            mode = b.dataset.mode;
            render();
        });
    });

    show('미니맵 불러오는 중…');
    fetch('/static/maps.json')
        .then(function (r) { return r.json(); })
        .then(function (list) {
            maps = {};
            list.forEach(function (m) { maps[m.uuid] = m; });
            // 탭 이름은 Riot 맵 UUID 기준 공식명으로 덮어쓴다 — DB 의 map_name 은
            // OCR/수기 입력이라 실제 맵과 어긋난 경기가 있다(uuid 가 권위).
            panel.querySelectorAll('[data-role="heat-maps"] .btn-tab').forEach(function (b) {
                var meta = maps[b.dataset.uuid];
                if (!meta) return;
                var count = b.querySelector('.muted');
                b.firstChild.textContent = meta.name + ' ';
                if (count) b.appendChild(count);
            });
            render();
        })
        .catch(function () { show('미니맵 정보를 불러오지 못했어요.'); });
})();

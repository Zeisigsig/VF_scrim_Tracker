/* 개인 페이지: 요원별 성과 정렬·페이지, 날짜별 내전 버튼 페이지.
   서버가 전체 행/버튼을 렌더링하고 여기서는 정렬·숨김만 한다(JS 없어도 내용은 보임). */
(function () {
    var AGENT_PER_PAGE = 5;
    var MATCH_PER_PAGE = 4;   // 내전은 짝수 단위로 열려 한 줄에 4개가 맞다

    /** 한 페이지 분량만 보이게 하고 '‹ 1/N ›' 라벨을 갱신한다. */
    function paginate(items, pager, page, perPage) {
        var pages = Math.max(1, Math.ceil(items.length / perPage));
        page = Math.min(Math.max(page, 0), pages - 1);
        items.forEach(function (el, i) {
            el.hidden = (i < page * perPage || i >= (page + 1) * perPage);
        });
        if (pager) {
            pager.hidden = items.length <= perPage;
            var label = pager.querySelector('.pager-label');
            if (label) label.textContent = (page + 1) + ' / ' + pages;
            pager.querySelectorAll('.btn-page').forEach(function (b) {
                var step = parseInt(b.dataset.step, 10);
                b.disabled = (page + step < 0 || page + step >= pages);
            });
        }
        return page;
    }

    function bindPager(pager, onChange) {
        if (!pager) return;
        pager.querySelectorAll('.btn-page').forEach(function (b) {
            b.addEventListener('click', function () {
                onChange(parseInt(b.dataset.step, 10));
            });
        });
    }

    /* --- 요원별 성과: 판수 / 승률 / 평균 점수 정렬 + 5개씩 --- */
    var tbody = document.querySelector('[data-role="agent-rows"]');
    if (tbody) {
        var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
        var pager = document.querySelector('[data-role="agent-pager"]');
        var page = 0;
        var key = 'games';

        function sortRows() {
            var sorted = rows.slice().sort(function (a, b) {
                var av = parseFloat(a.dataset[key]), bv = parseFloat(b.dataset[key]);
                if (bv !== av) return bv - av;
                // 동률이면 판수 많은 쪽 먼저 (3판 100% 가 20판 70% 위로 오지 않게)
                return parseFloat(b.dataset.games) - parseFloat(a.dataset.games);
            });
            sorted.forEach(function (r) { tbody.appendChild(r); });
            rows = sorted;
        }

        function render() { page = paginate(rows, pager, page, AGENT_PER_PAGE); }

        document.querySelectorAll('[data-role="agent-sort"] .btn-tab').forEach(function (btn) {
            btn.addEventListener('click', function () {
                document.querySelectorAll('[data-role="agent-sort"] .btn-tab')
                    .forEach(function (b) { b.classList.remove('active'); });
                btn.classList.add('active');
                key = btn.dataset.sort;
                page = 0;
                sortRows();
                render();
            });
        });
        bindPager(pager, function (step) { page += step; render(); });
        sortRows();
        render();
    }

    /* --- 날짜별 내전 버튼: 4개씩 --- */
    var list = document.querySelector('[data-role="match-list"]');
    if (list) {
        var btns = Array.prototype.slice.call(list.querySelectorAll('.match-btn'));
        var mPager = document.querySelector('[data-role="match-pager"]');
        var mPage = 0;
        var draw = function () { mPage = paginate(btns, mPager, mPage, MATCH_PER_PAGE); };
        bindPager(mPager, function (step) { mPage += step; draw(); });
        draw();
    }
})();

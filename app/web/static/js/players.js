// 유저 관리 페이지: 검색/필터/정렬 + 저장 AJAX(제자리 갱신). Jinja 값 없음.
(function () {
    const tbody = document.getElementById('players-tbody');
    if (!tbody) return;
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const search = document.getElementById('pf-search');
    const noDiscord = document.getElementById('pf-nodiscord');
    const noTier = document.getElementById('pf-notier');
    const sort = document.getElementById('pf-sort');
    const count = document.getElementById('pf-count');
    const hint = document.getElementById('pf-hint');

    function applyView() {
        const q = search.value.trim().toLowerCase();
        const onlyD = noDiscord.checked;
        const onlyT = noTier.checked;
        // 기본 상태(검색어·필터 없음)에서는 아무도 표시하지 않는다.
        const active = q !== '' || onlyD || onlyT;
        let shown = 0;
        for (const r of rows) {
            if (!active) { r.style.display = 'none'; continue; }
            const hay = r.dataset.name + ' ' + r.dataset.discord + ' ' + r.dataset.aliases;
            const matchQ = !q || hay.includes(q);
            const matchD = !onlyD || !r.dataset.discord;
            const matchT = !onlyT || r.dataset.tier === '';
            const vis = matchQ && matchD && matchT;
            r.style.display = vis ? '' : 'none';
            if (vis) shown++;
        }
        const mode = sort.value;
        const sorted = rows.slice().sort((a, b) => {
            if (mode === 'name') return a.dataset.name.localeCompare(b.dataset.name, 'ko');
            const ta = a.dataset.tier === '' ? -1 : parseFloat(a.dataset.tier);
            const tb = b.dataset.tier === '' ? -1 : parseFloat(b.dataset.tier);
            return mode === 'tier-asc' ? ta - tb : tb - ta;
        });
        for (const r of sorted) tbody.appendChild(r);
        if (hint) hint.style.display = active ? 'none' : '';
        count.textContent = active ? shown + '명 표시' : '';
    }
    search.addEventListener('input', applyView);
    noDiscord.addEventListener('change', applyView);
    noTier.addEventListener('change', applyView);
    sort.addEventListener('change', applyView);

    // 계정 관리: 검색해야 계정이 뜨도록(기본 숨김).
    const acSearch = document.getElementById('ac-search');
    const acHint = document.getElementById('ac-hint');
    const acBody = document.getElementById('accounts-tbody');
    if (acSearch && acBody) {
        const acRows = Array.from(acBody.querySelectorAll('tr'));
        function acView() {
            const q = acSearch.value.trim().toLowerCase();
            for (const r of acRows) {
                r.style.display = q !== '' && r.dataset.search.includes(q) ? '' : 'none';
            }
            if (acHint) acHint.style.display = q ? 'none' : '';
        }
        acSearch.addEventListener('input', acView);
        acView();
    }

    function flash(btn, text) {
        const orig = btn.dataset.orig || btn.textContent;
        btn.dataset.orig = orig;
        btn.textContent = text;
        btn.disabled = true;
        setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 1200);
    }

    function updateRow(form) {
        const r = form.closest('tr');
        const kind = form.dataset.kind;
        if (kind === 'edit') {
            const name = form.display_name.value.trim();
            const discord = form.discord_name.value.trim();
            r.dataset.name = name.toLowerCase();
            r.dataset.discord = discord.toLowerCase();
            r.querySelector('.label-link').textContent = discord ? discord + ' (' + name + ')' : name;
        } else if (kind === 'tier') {
            const v = parseFloat(form.tier_value.value);
            r.dataset.tier = String(v);
            r.querySelector('.tier-cell').textContent = v.toFixed(1);
            for (const o of form.tier_value.options) o.selected = (o.value === form.tier_value.value);
        } else if (kind === 'alias') {
            const a = form.alias.value.trim();
            if (a) {
                const cell = r.querySelector('.alias-cell');
                cell.textContent = cell.textContent.trim() ? cell.textContent.trim() + ', ' + a : a;
                r.dataset.aliases = r.dataset.aliases ? r.dataset.aliases + ',' + a.toLowerCase() : a.toLowerCase();
                form.alias.value = '';
            }
        } else if (kind === 'riot-del') {
            const chip = form.closest('.riot-chip');
            if (chip) chip.remove();
        } else if (kind === 'riot-add') {
            const name = form.riot_name.value.trim();
            const tag = form.riot_tag.value.trim().replace(/^#/, '');
            if (!name || !tag) return;
            const cell = form.closest('.riot-cell');
            const key = (name + '#' + tag).toLowerCase();
            if (cell.querySelector('.riot-chip[data-riot="' + key + '"]')) {
                form.riot_name.value = ''; form.riot_tag.value = ''; return;
            }
            const action = form.action.replace(/\/riot$/, '/riot/delete');
            const chip = document.createElement('span');
            chip.className = 'riot-chip';
            chip.dataset.riot = key;
            chip.innerHTML =
                '<span class="riot-id"></span>' +
                '<form class="inline riot-del-form" method="post" data-ajax data-kind="riot-del">' +
                '<input type="hidden" name="riot_name"><input type="hidden" name="riot_tag">' +
                '<button class="chip-x" type="submit" title="삭제">×</button></form>';
            chip.querySelector('.riot-id').textContent = name + '#' + tag;
            const delForm = chip.querySelector('form');
            delForm.action = action;
            delForm.riot_name.value = name;
            delForm.riot_tag.value = tag;
            cell.insertBefore(chip, form);
            attachAjax(delForm);
            form.riot_name.value = ''; form.riot_tag.value = '';
        }
    }

    function attachAjax(form) {
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = form.querySelector('button[type=submit]');
            try {
                const res = await fetch(form.action, { method: 'POST', body: new FormData(form) });
                if (!res.ok) throw new Error(res.status);
                updateRow(form);
                applyView();
                flash(btn, form.dataset.kind === 'riot-del' ? '×' : '저장됨');
            } catch (err) {
                flash(btn, '실패');
            }
        });
    }

    tbody.querySelectorAll('form[data-ajax]').forEach(attachAjax);

    applyView();
})();

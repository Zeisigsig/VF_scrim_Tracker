// 검토 화면: 요원→역할 자동, Enter 오제출 방지, 닉네임 매칭 미리보기.
// 숫자 필터(.numonly)는 numonly.js 가 담당. 요원-역할 규칙은 JSON 태그로 주입.
(function () {
    const roleData = document.getElementById('agent-role-data');
    const AGENT_ROLE = roleData ? JSON.parse(roleData.textContent) : {};

    document.querySelectorAll('.agent-select').forEach(function (sel) {
        sel.addEventListener('change', function () {
            const role = AGENT_ROLE[sel.value];
            if (!role) return;
            const target = document.querySelector('.role-select[data-row="' + sel.dataset.row + '"]');
            if (target) target.value = role;
        });
    });

    // 엔터로 폼이 제출돼 실수로 확정되던 문제 방지: input 에서 Enter 는 제출 대신
    // 닉네임이면 매칭 미리보기, 그 외 필드는 무시. '확정 및 저장'은 클릭만 허용.
    const reviewForm = document.querySelector('form[action$="/confirm"]');
    if (reviewForm) {
        reviewForm.addEventListener('keydown', function (e) {
            if (e.key !== 'Enter' || e.target.tagName !== 'INPUT') return;
            e.preventDefault();
            if (e.target.classList.contains('nick-input')) resolveNick(e.target);
        });
    }
    // 닉을 고치고 포커스를 벗어나도(blur) 즉시 갱신되게.
    document.querySelectorAll('.nick-input').forEach(function (inp) {
        inp.addEventListener('blur', function () { resolveNick(inp); });
    });

    async function resolveNick(input) {
        const row = input.dataset.row;
        const name = input.value.trim();
        const preview = document.querySelector('.nick-preview[data-row="' + row + '"]');
        const sel = document.querySelector('.res-select[data-row="' + row + '"]');
        if (!name) { if (preview) preview.textContent = ''; return; }
        let data;
        try {
            const res = await fetch('/api/resolve-nick?name=' + encodeURIComponent(name));
            data = await res.json();
        } catch (err) { return; }
        if (data.matched) {
            const val = 'existing:' + data.player_id;
            let opt = sel.querySelector('option[value="' + val + '"]');
            if (!opt) { opt = document.createElement('option'); opt.value = val; sel.appendChild(opt); }
            opt.textContent = '→ ' + data.label + ' (매칭)';
            sel.value = val;
            if (preview) { preview.textContent = '✓ ' + data.label + '(으)로 매칭됨'; preview.style.color = 'var(--green)'; }
        } else {
            sel.value = 'new';
            if (preview) { preview.textContent = '+ 신규 유저로 생성됨'; preview.style.color = 'var(--muted)'; }
        }
    }
})();

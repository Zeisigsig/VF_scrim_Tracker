// 로그인 화면 티어 분포 도넛. 데이터는 #tier-data(JSON)에서.
(function () {
    const dataEl = document.getElementById('tier-data');
    const canvas = document.getElementById('tierChart');
    if (!dataEl || !canvas) return;
    const { labels, counts } = JSON.parse(dataEl.textContent);
    const tierColors = {
        '아이언': '#6b7280', '브론즈': '#a16207', '실버': '#9ca3af', '골드': '#eab308',
        '플래티넘': '#22d3ee', '다이아': '#c084fc', '초월': '#4ade80', '불멸': '#f43f5e',
        '레디언트': '#fde047', '미설정': '#3f3f46'
    };
    new Chart(canvas, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: counts,
                backgroundColor: labels.map(t => (tierColors[t] || '#64748b')),
                borderColor: '#1a1c20', borderWidth: 2,
            }]
        },
        options: {
            plugins: { legend: { position: 'right', labels: { color: '#e6e6e6' } } }
        }
    });
})();

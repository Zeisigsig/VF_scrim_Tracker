// 개인 페이지 차트: 표시점수 추이(라인) + 맵/요원 능력치(레이더).
// 각 데이터는 해당 JSON 태그가 있을 때만 그린다(#trend-data/#map-data/#agent-data).
(function () {
    const trendEl = document.getElementById('trend-data');
    if (trendEl && document.getElementById('trendChart')) {
        const { labels, scores } = JSON.parse(trendEl.textContent);
        new Chart(document.getElementById('trendChart'), {
            type: 'line',
            data: {
                labels: labels.map(s => s.slice(0, 10)),
                datasets: [{
                    label: '표시 점수', data: scores,
                    borderColor: '#ff4655', backgroundColor: 'rgba(255,70,85,0.15)',
                    tension: 0.25, fill: true, pointRadius: 3,
                }]
            },
            options: {
                scales: { y: { suggestedMin: 0, suggestedMax: 1000, grid: { color: '#2c3038' } },
                          x: { grid: { color: '#2c3038' } } },
                plugins: { legend: { labels: { color: '#e6e6e6' } } }
            }
        });
    }

    function radar(id, dataElId, color) {
        const el = document.getElementById(id);
        const dataEl = document.getElementById(dataElId);
        if (!el || !dataEl) return;
        const { labels, data } = JSON.parse(dataEl.textContent);
        new Chart(el, {
            type: 'radar',
            data: {
                labels: labels,
                datasets: [{
                    label: '평균 표시 점수', data: data,
                    borderColor: color, backgroundColor: color + '33',
                    pointBackgroundColor: color, borderWidth: 2,
                }]
            },
            options: {
                scales: { r: {
                    suggestedMin: 0, suggestedMax: 1000,
                    angleLines: { color: '#2c3038' }, grid: { color: '#2c3038' },
                    pointLabels: { color: '#e6e6e6' },
                    ticks: { color: '#8a8f99', backdropColor: 'transparent', showLabelBackdrop: false },
                } },
                plugins: { legend: { labels: { color: '#e6e6e6' } } }
            }
        });
    }
    radar('mapChart', 'map-data', '#38bdf8');
    radar('agentChart', 'agent-data', '#a78bfa');
})();

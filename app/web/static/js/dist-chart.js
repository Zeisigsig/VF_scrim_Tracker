// 홈 조정점수 분포 곡선 + '나' 위치 화살표. 데이터는 #dist-data(JSON)에서.
(function () {
    const dataEl = document.getElementById('dist-data');
    const canvas = document.getElementById('distChart');
    if (!dataEl || !canvas) return;
    const { counts, my_frac: myFrac } = JSON.parse(dataEl.textContent);
    // 곡선(tension) 스플라인이 데이터 최댓값 위로 넘칠 수 있어 y 상한에 여유를 준다.
    const yMax = Math.max(...counts) * 1.35 || 1;
    const arrowPlugin = {
        id: 'myArrow',
        afterDraw(chart) {
            if (myFrac === null) return;
            const ctx = chart.ctx, a = chart.chartArea;
            const x = a.left + myFrac * (a.right - a.left);
            ctx.save();
            ctx.strokeStyle = 'rgba(255,70,85,0.6)';
            ctx.lineWidth = 2; ctx.setLineDash([4, 4]);
            ctx.beginPath(); ctx.moveTo(x, a.top); ctx.lineTo(x, a.bottom); ctx.stroke();
            ctx.setLineDash([]);
            ctx.fillStyle = '#ff4655';
            ctx.beginPath();
            ctx.moveTo(x, a.bottom + 3);
            ctx.lineTo(x - 6, a.bottom + 13);
            ctx.lineTo(x + 6, a.bottom + 13);
            ctx.closePath(); ctx.fill();
            ctx.fillStyle = '#e6e6e6'; ctx.font = '12px sans-serif'; ctx.textAlign = 'center';
            ctx.fillText('나', x, a.bottom + 27);
            ctx.restore();
        }
    };
    new Chart(canvas, {
        type: 'line',
        data: {
            labels: counts.map(() => ''),
            datasets: [{
                data: counts, borderColor: '#7db3ff',
                backgroundColor: 'rgba(125,179,255,0.15)',
                fill: true, tension: 0.45, pointRadius: 0, borderWidth: 2,
            }]
        },
        options: {
            layout: { padding: { bottom: 30 } },
            plugins: { legend: { display: false }, tooltip: { enabled: false } },
            scales: { x: { display: false }, y: { display: false, beginAtZero: true, max: yMax } }
        },
        plugins: [arrowPlugin]
    });
})();

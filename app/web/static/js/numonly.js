// 숫자 전용 입력칸(.numonly): 숫자 외 문자를 즉시 제거(스피너 없는 text 입력 오입력 방지).
document.querySelectorAll('.numonly').forEach(function (inp) {
    inp.addEventListener('input', function () {
        const cleaned = inp.value.replace(/[^0-9]/g, '');
        if (cleaned !== inp.value) inp.value = cleaned;
    });
});

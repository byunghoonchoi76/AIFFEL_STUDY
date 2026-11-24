from app.analysis.matching import expand_quote


def test_expand_short_quote_to_sentence():
    text = "관영 매체는 이달 하순부터 정기 동계훈련을 실시한다고 공표했다. 주민들은 전차 행렬을 목격했다."
    assert expand_quote("공표했다", text) == "관영 매체는 이달 하순부터 정기 동계훈련을 실시한다고 공표했다."
    assert expand_quote("전차 행렬", text) == "주민들은 전차 행렬을 목격했다."


def test_expand_keeps_quote_when_sentence_too_long():
    long = "가" * 200 + " 포성이 이어지고 " + "나" * 50 + "."
    assert expand_quote("포성이 이어지고", long) == "포성이 이어지고"


def test_summary_adds_most_probable_when_different():
    from types import SimpleNamespace as N
    from app.analysis.explain import add_most_probable
    atk = N(hypothesis_id="H_ATTACK", name="공격 준비", probability=0.34)
    ex = N(hypothesis_id="H_EXERCISE", name="대규모 훈련", probability=0.46)
    res = N(hypotheses=[atk, ex], top=atk)
    out = add_most_probable({"text": "… '공격 준비' 가능성 34%로 판단됨.", "citations": []}, res)
    assert "가장 유력한 가설은 '대규모 훈련'(46%)" in out["text"]
    res2 = N(hypotheses=[atk, ex], top=ex)
    assert add_most_probable({"text": "x"}, res2)["text"] == "x"

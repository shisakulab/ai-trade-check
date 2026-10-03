"""売買判断エンジン。既定はダミー（SMAクロス）。自分で ask_ai を実装すると AI の判断に切り替えられる。"""

# ask_ai を実装したら True にする（False のあいだは "ai" を選んでもダミーで判断する）
AI_READY = False


def engine_name(setting):
    """"dummy"（既定）か "ai" を返す。"ai" は ask_ai を実装済み（AI_READY = True）のときだけ。"""
    if setting == "ai" and AI_READY:
        return "ai"
    return "dummy"


def decide(engine, state, features):
    """(action, confidence, raw) を返す。AI の呼び出しに失敗したら安全側の hold にする。"""
    if engine == "ai":
        try:
            action, conf, raw = ask_ai(state)
            if action not in ("buy", "sell", "hold"):
                raise ValueError(f"想定外の回答: {action}")
            return action, float(conf), raw
        except Exception as e:
            return "hold", 0.0, f"ai error: {e}"
    return dummy(features)


def veto(state):
    """モメンタムの買い候補を止めるか。(avoid: bool, confidence, raw) を返す。既定は止めない。"""
    return False, 0.0, "rule"


def ask_ai(state):
    """自分の使う AI（LLM の API など）に、state（銘柄・特徴量・持ち高）を渡して、
    ('buy'|'sell'|'hold', 確信度0〜1, 生の応答の文字列) を返す関数をここに書く。"""
    raise NotImplementedError("ask_ai を実装してください")


def dummy(f):
    """AI を使わないとき用。5日線と25日線のクロスで判断する。"""
    crossed_up = f["sma5_prev"] <= f["sma25_prev"] and f["sma5"] > f["sma25"]
    crossed_down = f["sma5_prev"] >= f["sma25_prev"] and f["sma5"] < f["sma25"]
    if crossed_up and f["rsi14"] < 70:
        return "buy", 0.7, "dummy: golden cross"
    if crossed_down or f["rsi14"] > 80:
        return "sell", 0.7, "dummy: dead cross / overbought"
    return "hold", 0.5, "dummy: no signal"

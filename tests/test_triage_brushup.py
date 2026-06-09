"""ダイジェスト ブラッシュアップ：分類順是正・is_actionable・表示ポリシー(_should_show)。"""
from __future__ import annotations

from aiia.config import UserConfig
from aiia.pipeline import _should_show
from aiia.schemas import Category, ClassificationResult, EmailMessage, EmailThread
from aiia.triage import classify_from_hints, heuristic_signals


def _thread(*, sender: str, domain: str, subject: str, body: str, headers: dict | None = None) -> EmailThread:
    return EmailThread(
        thread_id="t", subject=subject,
        messages=[EmailMessage(message_id="m", sender=sender, sender_domain=domain,
                               subject=subject, body_text=body, headers=headers or {})],
    )


def _user() -> UserConfig:
    return UserConfig(display_name="小俣翔碁", internal_domain="vectorinc.co.jp",
                      client_domains=["bigclient.ae"])


# ── 分類順是正：社内エイリアスの一括配信は NEWSLETTER（INTERNALにしない）─────────
def test_internal_alias_newsletter_is_newsletter() -> None:
    th = _thread(sender="'Instagram' via タテガタ <tategata15@vectorinc.co.jp>",
                 domain="vectorinc.co.jp", subject="見逃したコンテンツをチェックしよう",
                 body="フォロー中アカウントの新着投稿", headers={"List-Unsubscribe": "<https://x/u>"})
    cls = classify_from_hints(heuristic_signals(th, _user()))
    assert cls.category == Category.NEWSLETTER  # 社内ドメインでもNL優先


def test_real_internal_mail_stays_internal() -> None:
    th = _thread(sender="同僚 <colleague@vectorinc.co.jp>", domain="vectorinc.co.jp",
                 subject="資料の件", body="共有です")  # List-Unsubscribe なし
    cls = classify_from_hints(heuristic_signals(th, _user()))
    assert cls.category == Category.INTERNAL


def test_internal_mail_with_press_words_is_not_press() -> None:
    # 社内連絡/業務日報が「記者/メディア」語で PRESS 誤検知しない（社外のみPRESS）
    th = _thread(sender="a-moriya@vectorinc.co.jp", domain="vectorinc.co.jp",
                 subject="【18Fスタジオ】記者発表会のため通り抜けご遠慮", body="終日使用します")
    assert classify_from_hints(heuristic_signals(th, _user())).category == Category.INTERNAL
    # 社外の取材依頼は PRESS のまま
    ext = _thread(sender="reporter@press.example", domain="press.example",
                  subject="取材のご依頼", body="インタビューさせてください")
    assert classify_from_hints(heuristic_signals(ext, _user())).category == Category.PRESS_MEDIA


# ── is_actionable ───────────────────────────────────────────────────────────
def test_actionable_true_on_request() -> None:
    th = _thread(sender="a@vectorinc.co.jp", domain="vectorinc.co.jp", subject="ご相談",
                 body="本日中にご確認いただけますか？")
    assert heuristic_signals(th, _user()).is_actionable is True


def test_actionable_false_on_newsletter_and_completion() -> None:
    nl = _thread(sender="noreply@x.example", domain="x.example", subject="お知らせ",
                 body="新着です", headers={"List-Unsubscribe": "<x>"})
    assert heuristic_signals(nl, _user()).is_actionable is False  # NLは対応不要
    done = _thread(sender="vec_soumu@vectorinc.co.jp", domain="vectorinc.co.jp",
                   subject="※終了しました※Re: 通り抜けご遠慮", body="ご協力ありがとうございました")
    assert heuristic_signals(done, _user()).is_actionable is False  # 完了連絡


# ── メーリングリスト（List-Id/Precedence）→ NEWSLETTER畳み＋安全弁 ──────────────
def test_mailing_list_via_list_id_is_newsletter() -> None:
    th = _thread(sender="team@vectorinc.co.jp", domain="vectorinc.co.jp", subject="部署連絡",
                 body="共有です", headers={"List-Id": "<team.vectorinc.co.jp>"})
    h = heuristic_signals(th, _user())
    assert h.is_mailing_list is True
    assert classify_from_hints(h).category == Category.NEWSLETTER  # ML→畳み対象


def test_mailing_list_actionable_escalates_via_should_show() -> None:
    # ML でも 名指し/締切なら is_actionable=True → _should_show で個別に昇格（安全弁）
    th = _thread(sender="all@vectorinc.co.jp", domain="vectorinc.co.jp", subject="至急のお願い",
                 body="小俣翔碁さん 本日中にご確認いただけますか？",
                 headers={"List-Id": "<all.vectorinc.co.jp>"})
    cls = classify_from_hints(heuristic_signals(th, _user()))
    assert cls.category == Category.NEWSLETTER and cls.is_actionable is True
    assert _should_show(cls) is True  # 畳まず出す


# ── To / Cc 分離 ─────────────────────────────────────────────────────────────
def test_to_vs_cc_recipient_kind() -> None:
    me = "s-komata@vectorinc.co.jp"
    u = UserConfig(user_id=me, display_name="小俣翔碁", internal_domain="vectorinc.co.jp")
    to_th = _thread(sender="x@a.example", domain="a.example", subject="ご確認",
                    body="ご確認ください", headers={"To": me, "Cc": "other@a.example"})
    cc_th = _thread(sender="x@a.example", domain="a.example", subject="共有",
                    body="参考まで", headers={"To": "boss@a.example", "Cc": f"team@a.example, {me}"})
    assert heuristic_signals(to_th, u).is_to is True
    hcc = heuristic_signals(cc_th, u)
    assert hcc.is_cc is True and hcc.is_to is False
    assert classify_from_hints(heuristic_signals(to_th, u)).recipient_kind == "to"
    assert classify_from_hints(hcc).recipient_kind == "cc"


# ── 表示ポリシー _should_show ─────────────────────────────────────────────────
def _cls(cat: Category, *, actionable: bool = False, vip: bool = False) -> ClassificationResult:
    return ClassificationResult(category=cat, confidence=0.9, is_actionable=actionable, is_vip=vip)


def test_should_show_policy() -> None:
    # 個別表示：要返信 or 重要cat or VIP
    assert _should_show(_cls(Category.INTERNAL, actionable=True)) is True   # 社内でも要返信なら出す
    assert _should_show(_cls(Category.CLIENT_NORMAL)) is True               # 顧客は重要catで出す
    assert _should_show(_cls(Category.PRESS_MEDIA)) is True
    assert _should_show(_cls(Category.NEWSLETTER, vip=True)) is True        # VIPなら出す
    # 畳む：非アクションの一般/社内FYI
    assert _should_show(_cls(Category.INTERNAL)) is False                   # 社内FYI→畳む
    assert _should_show(_cls(Category.NEWSLETTER)) is False
    assert _should_show(_cls(Category.VENDOR_PARTNER)) is False

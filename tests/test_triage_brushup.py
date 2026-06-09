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

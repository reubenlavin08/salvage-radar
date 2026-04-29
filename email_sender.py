"""Gmail SMTP digest emailer."""
import smtplib
import ssl
from email.message import EmailMessage
from html import escape

import config


def send_email(subject: str, html_body: str, text_body: str,
               username: str, password: str) -> bool:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.EMAIL_FROM
    msg["To"] = config.EMAIL_TO
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(username, password)
            s.send_message(msg)
        return True
    except Exception as e:
        print(f"Email send failed: {e}")
        return False


def build_digest_html(listings: list, header: str = None) -> str:
    if not listings and not header:
        return ""
    rows = []
    tier_color = {
        "A": "#cce5ff", "B": "#e2e3e5", "C": "#fefefe",
        "D": "#fff3cd", "unknown": "#f8f9fa",
    }
    for L in sorted(listings, key=lambda x: -x["score"]):
        bg = tier_color.get(L["tier"], "#fff")
        snippet = escape((L.get("body", "") or "")[:200])
        dist = L.get("distance_km")
        dist_str = f"{dist:.1f} km" if dist is not None else "?"
        geo_marker = {"coords": "✓", "string": "~", "none": "?"}.get(
            L.get("geo_source", ""), "")
        price_str = f"${L['ask_price']}"
        if L.get("price_uncertain"):
            price_str += " (?)"
        rows.append(f"""
        <tr style="background:{bg}">
          <td><a href="{escape(L['link'])}">{escape(L['title'])}</a></td>
          <td>{price_str}</td>
          <td>${L['salvage_estimate']}</td>
          <td>{L['score']:.0f}</td>
          <td>{escape(L['tier'])} {geo_marker}</td>
          <td>{dist_str}</td>
          <td>{escape(L.get('neighborhood','') or '')}</td>
          <td>{escape((L.get('posted_at','') or '')[:16])}</td>
          <td>{snippet}</td>
        </tr>""")
    table = ""
    if rows:
        table = f"""<table cellpadding="6" cellspacing="0" border="1"
        style="border-collapse:collapse;font-size:13px;">
        <tr><th>Title</th><th>Price</th><th>Est. salvage</th><th>Score</th>
            <th>Tier</th><th>Dist</th><th>Neighborhood</th>
            <th>Posted</th><th>Snippet</th></tr>
        {''.join(rows)}
        </table>
        <p style="color:#888;font-size:11px;">Tier A (blue) = ≤2.5 km from
        Dunbar. B = ≤4.5 km. C = ≤7 km. D (yellow) = ≤9 km stretch.
        Tier marker: ✓ = confirmed by map coords, ~ = neighborhood string,
        ? = needs review. Price (?) = "make me an offer" / OBO listings.</p>"""
    head = f"<h2>{escape(header)}</h2>" if header else ""
    return f"""<html><body style="font-family:system-ui,sans-serif;">
    {head}{table}</body></html>"""

# Instagram_Data_Collection.py
# ------------------------------------------------------------
# Scrapes recent Instagram posts for one or more usernames and
# writes a single-row CSV summary per account with:
# - avg likes/comments
# - view-adjusted ER (for posts with views, e.g., Reels/Video)
# - posts/week
# - hashtag efficiency
# - best posting windows (hour / weekday)
# - caption length vs ER (r + buckets)
# - content category lift (based on simple theme guess)
#
# Notes:
# - IG does not expose saves/shares publicly; those are kept as 0.
# - "Views" exist for Reels/Video; for Photos they are typically unavailable.
# - CSV column names match your original schema (e.g., "tiktok_profile_name"
#   holds the Instagram display name).
#
# Setup (recommended versions):
#   python -m pip install --upgrade pip
#   pip install "instagrapi==2.1.5"
#
# Run (Windows PowerShell, same terminal/venv):
#   $env:IG_USERNAME="your_instagram_username"
#   $env:IG_PASSWORD="your_instagram_password"
#   python Instagram_Data_Collection.py all.american.eng englishwiththisguy eslkate
# ------------------------------------------------------------

import os
import sys
import re
import csv
import statistics
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

# Console encoding fix for Windows (avoid UnicodeEncodeError on cp1250)
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# pip install instagrapi==2.1.5
from instagrapi import Client
from instagrapi.exceptions import ClientForbiddenError

# ---- HOTFIX: tolerate extract_user_gql signature mismatch in some builds (harmless if not needed) ----
try:
    import inspect
    import instagrapi.extractors as _ex
    if "update_headers" not in inspect.signature(_ex.extract_user_gql).parameters:
        _orig = _ex.extract_user_gql
        def _patched_extract_user_gql(data, update_headers=None):
            return _orig(data)
        _ex.extract_user_gql = _patched_extract_user_gql
except Exception:
    pass


# ------------ CONFIG ------------
POSTS_TO_FETCH = 25               # last N posts to analyze
MIN_HASHTAG_OCCURRENCES = 2       # for hashtag efficiency stats
SESSION_FILE = os.getenv("IG_SESSION_FILE", "ig_session.json")
# --------------------------------


# ---------- Utilities / parsing ----------
def extract_hashtags(text: str) -> List[str]:
    return re.findall(r"#\w+", text or "")


def guess_theme(hashtags: List[str], caption: str) -> str:
    blob = (" ".join(hashtags) + " " + (caption or "")).lower()
    themes = [
        ("grammar",         ["grammar", "grammartips", "pasttense", "presentperfect", "articles", "tenses"]),
        ("vocabulary",      ["vocabulary", "vocab", "wordoftheday", "phrases", "idioms", "phrasalverbs"]),
        ("pronunciation",   ["pronunciation", "accent", "phonetics", "ipa", "sounds"]),
        ("exam/test prep",  ["ielts", "toefl", "toeic", "cambridge", "pte"]),
        ("slang/culture",   ["slang", "culture", "britishvsamerican", "usvsuk"]),
        ("business english",["businessenglish", "interview", "resume", "cv", "email"]),
        ("study tips",      ["study", "tips", "learnenglish", "englishlearning"]),
    ]
    for label, kws in themes:
        if any(kw in blob for kw in kws):
            return label
    return "general english"


def guess_country_from_bio(bio: str) -> str:
    if not bio:
        return "Unknown"
    bio_low = bio.lower()
    mapping = {
        "United States": ["usa", "us", "america", "american"],
        "United Kingdom": ["uk", "united kingdom", "british", "england"],
        "Canada": ["canada", "canadian"],
        "Australia": ["australia", "aussie", "australian"],
        "India": ["india", "indian"],
        "Poland": ["poland", "polish"],
        "France": ["france", "french"],
        "Germany": ["germany", "german"],
        "Spain": ["spain", "spanish"],
        "Italy": ["italy", "italian"],
        "Brazil": ["brazil", "brazilian"],
        "Mexico": ["mexico", "mexican"],
        "China": ["china", "chinese"],
        "Japan": ["japan", "japanese"],
        "Korea": ["korea", "korean"],
        "Turkey": ["turkey", "turkish"],
    }
    for country, kws in mapping.items():
        if any(kw in bio_low for kw in kws):
            return country
    return "Unknown"


def posts_per_week(timestamps: List[datetime]) -> Optional[float]:
    ts = [t for t in timestamps if isinstance(t, datetime)]
    if len(ts) < 2:
        return None
    ts.sort()
    days = (ts[-1] - ts[0]).days or 1
    return len(ts) / (days / 7.0) if days > 0 else None


def pearson_r(xs: List[float], ys: List[float]) -> Optional[float]:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = (sum((x - mean_x) ** 2 for x in xs)) ** 0.5
    den_y = (sum((y - mean_y) ** 2 for y in ys)) ** 0.5
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


# ---------- Instagram Client ----------
def ig_login() -> Client:
    user = os.getenv("IG_USERNAME")
    pwd = os.getenv("IG_PASSWORD")
    if not user or not pwd:
        raise RuntimeError("Set IG_USERNAME and IG_PASSWORD environment variables")
    cl = Client()
    # Reuse session if possible (reduces 2FA prompts)
    if os.path.exists(SESSION_FILE):
        try:
            cl.load_settings(SESSION_FILE)
        except Exception:
            pass
    cl.login(user, pwd)
    try:
        cl.dump_settings(SESSION_FILE)
    except Exception:
        pass
    return cl


# ---------- Scraping (private-only) ----------
def get_profile_identity(cl: Client, username: str):
    """
    Private-only flow to avoid flaky web/GraphQL calls.
    """
    user_pk = cl.user_id_from_username(username)  # private endpoint
    info = cl.user_info(user_pk)                  # private endpoint
    display_name = info.full_name or username
    bio_text     = info.biography or ""
    followers    = info.follower_count or 0
    following    = info.following_count or 0
    return display_name, bio_text, followers, following, user_pk


def collect_recent_medias(cl: Client, user_pk: int, limit: int):
    """
    Prefer private endpoints; never rely on public GraphQL.
    Order: user_medias_v1 -> user_medias -> (optional) user_medias_gql (disabled).
    """
    # 1) private mobile API
    try:
        medias = cl.user_medias_v1(user_pk, amount=limit)
        if medias:
            return medias[:limit]
    except Exception:
        pass
    # 2) generic (often private under the hood)
    try:
        medias = cl.user_medias(user_pk, amount=limit)
        if medias:
            return medias[:limit]
    except Exception:
        pass
    # 3) (disabled) public GraphQL fallback – uncomment if you really want it
    # try:
    #     medias = cl.user_medias_gql(user_pk, amount=limit)
    #     if medias:
    #         return medias[:limit]
    # except Exception:
    #     pass
    return []


def _media_type_name(media_type: int) -> str:
    # 1=Photo, 2=Video, 8=Album (per instagrapi)
    return {1: "Photo", 2: "Video", 8: "Album"}.get(media_type, str(media_type))


def scrape_media(m) -> Dict:
    """
    Convert instagrapi Media -> our post dict.
    """
    views = getattr(m, "view_count", None) or getattr(m, "play_count", None) or 0
    likes = getattr(m, "like_count", 0) or 0
    comments = getattr(m, "comment_count", 0) or 0

    # IG doesn't expose shares/saves publicly
    shares = 0
    saves = 0

    caption = (getattr(m, "caption_text", "") or "").strip()
    hashtags = extract_hashtags(caption)
    caption_len = len(caption)
    ts = getattr(m, "taken_at", None)

    er_view = None
    if views and views > 0:
        er_view = (likes + comments) / views

    code = getattr(m, "code", None)
    url = f"https://www.instagram.com/p/{code}/" if code else ""
    theme = guess_theme(hashtags, caption)

    return {
        "url": url,
        "views": int(views or 0),
        "likes": int(likes or 0),
        "comments": int(comments or 0),
        "shares": shares,
        "saves": saves,
        "er_view": er_view,
        "caption": caption,
        "caption_len": caption_len,
        "hashtags": hashtags,
        "timestamp": ts,
        "theme": theme,
        "media_type": _media_type_name(getattr(m, "media_type", 0)),
    }


# ---------- Analysis helpers ----------
def hashtag_efficiency(posts: List[Dict], min_occurrences=2):
    ers = [p["er_view"] for p in posts if p.get("er_view") is not None]
    overall = statistics.mean(ers) if ers else 0.0
    bucket = defaultdict(list)
    for p in posts:
        if p.get("er_view") is None:
            continue
        for h in set(p.get("hashtags", [])):
            bucket[h.lower()].append(p["er_view"])
    rows = []
    for h, vals in bucket.items():
        if len(vals) >= min_occurrences:
            avg = statistics.mean(vals)
            lift = avg - overall
            rows.append((h, len(vals), avg, lift))
    rows.sort(key=lambda x: x[3], reverse=True)
    return overall, rows


def posting_window_performance(posts: List[Dict]):
    hour_bucket = defaultdict(list)
    weekday_bucket = defaultdict(list)
    for p in posts:
        if p.get("er_view") is None or not isinstance(p.get("timestamp"), datetime):
            continue
        ts = p["timestamp"]
        hour_bucket[ts.hour].append(p["er_view"])
        weekday_bucket[ts.weekday()].append(p["er_view"])  # 0=Mon

    def top_avg(bucket, topn=3):
        avgs = []
        for k, vals in bucket.items():
            if len(vals) >= 2:
                avgs.append((k, statistics.mean(vals), len(vals)))
        avgs.sort(key=lambda x: x[1], reverse=True)
        return avgs[:topn]

    return top_avg(hour_bucket), top_avg(weekday_bucket)


def caption_length_vs_er(posts: List[Dict]):
    pts = [(p["caption_len"], p["er_view"]) for p in posts if p.get("er_view") is not None]
    if not pts:
        return None, {}
    xs, ys = zip(*pts)
    r = pearson_r(list(xs), list(ys))
    bins = [(0, 20), (21, 40), (41, 60), (61, 80), (81, 120), (121, 9999)]
    labels = ["0-20", "21-40", "41-60", "61-80", "81-120", "121+"]
    bucket = {label: [] for label in labels}
    for length, er in pts:
        for (lo, hi), lab in zip(bins, labels):
            if lo <= length <= hi:
                bucket[lab].append(er)
                break
    bucket_avg = {lab: (statistics.mean(v) if v else 0.0, len(v)) for lab, v in bucket.items()}
    return r, bucket_avg


def content_category_lift(posts: List[Dict]):
    ers = [p["er_view"] for p in posts if p.get("er_view") is not None]
    overall = statistics.mean(ers) if ers else 0.0
    cat_bucket = defaultdict(list)
    for p in posts:
        if p.get("er_view") is None:
            continue
        cat_bucket[p.get("theme", "general english")].append(p["er_view"])
    rows = []
    for cat, vals in cat_bucket.items():
        if len(vals) >= 2:
            avg = statistics.mean(vals)
            lift = avg - overall
            rows.append((cat, len(vals), avg, lift))
    rows.sort(key=lambda x: x[3], reverse=True)
    return overall, rows


# ---------- CSV writer ----------
CSV_COLUMNS = [
    "tiktok_profile_name",         # keeps schema compatibility; stores Instagram display name
    "username",
    "posts_analyzed",
    "avg_likes",
    "avg_comments",
    "engagement_rate_view_adj_mean",
    "post_frequency_per_week",
    "content_type",
    "content_theme",
    "avg_shares",
    "avg_saves",
    "hashtags_used",
    "country_region",
    "hashtag_efficiency_top",
    "posting_window_performance",
    "caption_length_vs_er",
    "content_category_lift_top",
]


def write_profile_summary_csv(username: str, row: Dict):
    fname = f"{username}_summary.csv"
    with open(fname, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerow(row)
    print(f"Saved: {fname}")


# ---------- Orchestration / output ----------
def main(args: list):
    if len(args) >= 1:
        usernames = args
    else:
        env_user = os.getenv("IG_TARGET_USERNAME")
        if not env_user:
            print("Usage: python Instagram_Data_Collection.py <username1> <username2> ...")
            print("Or set IG_TARGET_USERNAME env var.")
            sys.exit(1)
        usernames = [env_user]

    cl = ig_login()

    try:
        for username in usernames:
            print(f"\n===== @{username} (Instagram) =====")

            # Profile identity with re-login retry
            try:
                display_name, bio, followers, following, user_pk = get_profile_identity(cl, username)
            except ClientForbiddenError:
                cl.relogin()
                display_name, bio, followers, following, user_pk = get_profile_identity(cl, username)

            medias = collect_recent_medias(cl, user_pk, POSTS_TO_FETCH)
            if not medias:
                print("No recent posts found or profile is private.")
                continue

            posts = []
            for i, m in enumerate(medias, 1):
                try:
                    posts.append(scrape_media(m))
                except Exception as e:
                    print(f"  (skip post {i}: {e})")

            # Aggregates
            likes_list    = [p["likes"]    for p in posts]
            comments_list = [p["comments"] for p in posts]
            shares_list   = [p["shares"]   for p in posts]
            saves_list    = [p["saves"]    for p in posts]
            timestamps    = [p["timestamp"] for p in posts if p["timestamp"]]

            avg_likes    = statistics.mean(likes_list) if likes_list else 0.0
            avg_comments = statistics.mean(comments_list) if comments_list else 0.0
            avg_shares   = statistics.mean(shares_list) if shares_list else 0.0
            avg_saves    = statistics.mean(saves_list) if saves_list else 0.0

            # View-adjusted ER (only where views exist)
            er_vals = [p["er_view"] for p in posts if p.get("er_view") is not None]
            er_mean = statistics.mean(er_vals) if er_vals else 0.0
            er_median = statistics.median(er_vals) if er_vals else 0.0  # not in CSV

            # Post frequency
            freq = posts_per_week(timestamps)
            post_freq = round(freq, 4) if freq else None

            # Theme majority
            themes = [p["theme"] for p in posts]
            try:
                content_theme = statistics.mode(themes) if themes else "general english"
            except statistics.StatisticsError:
                content_theme = "general english"

            # Country/Region guess
            country = guess_country_from_bio(bio)

            # Unique hashtags
            all_tags = []
            for p in posts:
                all_tags.extend(p["hashtags"])
            unique_tags = sorted(set(all_tags))
            hashtags_used_str = ";".join(unique_tags)

            # Hashtag efficiency (top 5)
            overall_er, tag_rows = hashtag_efficiency(posts, MIN_HASHTAG_OCCURRENCES)
            top_tags = [f"{tag}:{lift:+.4f}(n={n})" for tag, n, avg, lift in tag_rows[:5]]
            tag_eff_str = ";".join(top_tags) if top_tags else ""

            # Posting window performance
            hour_tops, weekday_tops = posting_window_performance(posts)
            hours_str = ",".join([f"h{h}@{avg:.4f}(n={n})" for h, avg, n in hour_tops]) if hour_tops else ""
            wd_map = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            wdays_str = ",".join([f"{wd_map[d]}@{avg:.4f}(n={n})" for d, avg, n in weekday_tops]) if weekday_tops else ""
            posting_perf_str = f"hours[{hours_str}]|weekdays[{wdays_str}]"

            # Caption length vs ER
            r, buckets = caption_length_vs_er(posts)
            if r is not None:
                bucket_parts = [f"{lab}:{avg:.4f}(n={n})" for lab, (avg, n) in buckets.items()]
                caption_vs_er_str = f"r={r:.3f}; " + ";".join(bucket_parts)
            else:
                caption_vs_er_str = "r=N/A"

            # Content category lift
            overall_cat, cat_rows = content_category_lift(posts)
            top_cats = [f"{cat}:{lift:+.4f}(n={n})" for cat, n, avg, lift in cat_rows[:5]]
            cat_lift_str = ";".join(top_cats) if top_cats else ""

            # Console summary (ASCII-only to avoid Unicode errors)
            print(f"Followers:              {followers:,}")
            print(f"Following:              {following:,}")
            print(f"Analyzed posts:         {len(posts)}")
            print(f"Avg Likes:              {avg_likes:,.2f}")
            print(f"Avg Comments:           {avg_comments:,.2f}")
            print(f"View-adjusted ER:       mean={er_mean:.4f}, median={er_median:.4f}")
            print(f"Post frequency:         {post_freq if post_freq is not None else 'Unknown'} posts/week")
            print(f"Content type:           Instagram (Post/Reel)")
            print(f"Content theme:          {content_theme}")
            print(f"Avg shares / saves:     {avg_shares:.2f} / {avg_saves:.2f}")
            print(f"Country/Region:         {country}")

            # CSV row (schema-compatible)
            row = {
                "tiktok_profile_name": display_name,
                "username": username,
                "posts_analyzed": len(posts),
                "avg_likes": round(avg_likes, 4),
                "avg_comments": round(avg_comments, 4),
                "engagement_rate_view_adj_mean": round(er_mean, 6),
                "post_frequency_per_week": round(post_freq, 4) if post_freq is not None else "",
                "content_type": "Instagram (Post/Reel)",
                "content_theme": content_theme,
                "avg_shares": round(avg_shares, 4) if avg_shares else 0.0,
                "avg_saves": round(avg_saves, 4) if avg_saves else 0.0,
                "hashtags_used": hashtags_used_str,
                "country_region": country,
                "hashtag_efficiency_top": tag_eff_str,
                "posting_window_performance": posting_perf_str,
                "caption_length_vs_er": caption_vs_er_str,
                "content_category_lift_top": cat_lift_str,
            }

            write_profile_summary_csv(username, row)

            # Per-post snapshot (ASCII-friendly)
            print("\nPer-post snapshot:")
            for i, p in enumerate(posts, 1):
                ts = p["timestamp"].strftime("%Y-%m-%d") if isinstance(p["timestamp"], datetime) else "?"
                cap = (p["caption"] or "").replace("\n", " ")
                if len(cap) > 60:
                    cap = cap[:57] + "..."
                er = p["er_view"]
                er_str = f"{er:.4f}" if er is not None else "NA"
                print(f" {i:02d}. views={p['views']:>7} | likes={p['likes']:>6} | comments={p['comments']:>5} | ER {er_str:>6} | {ts} | {cap}")

    finally:
        try:
            cl.logout()
        except Exception:
            pass


if __name__ == "__main__":
    main(sys.argv[1:])

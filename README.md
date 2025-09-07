# Instagram Insights Scraper

This repository contains a Python script that logs into Instagram (via [instagrapi](https://github.com/adw0rd/instagrapi)) and collects engagement insights from recent posts. It produces a **single-row CSV summary per account** with metrics such as:

- Average likes and comments  
- View-adjusted engagement rate (for Reels/Video posts)  
- Posting frequency (posts per week)  
- Hashtag efficiency (top-performing hashtags)  
- Best posting windows (by hour and weekday)  
- Caption length vs. engagement correlation  
- Content category lift (simple theme guess from hashtags/captions)  
- Country/region guess (based on bio text)  

---

## Features
- Pulls the last **N posts** per username (default = 25)  
- Uses Instagram private/mobile endpoints for more reliable data access  
- Exports results to CSV with consistent schema (compatible with TikTok schema columns)  
- Prints a per-post snapshot (views, likes, comments, ER, date, caption preview)  

---

## Installation

### 1. Clone the repository
```bash
git clone https://github.com/your-username/instagram-insights-scraper.git
cd instagram-insights-scraper
2. Set up Python environment
It’s recommended to use Python 3.9+.

bash
Copy code
python -m pip install --upgrade pip
pip install "instagrapi==2.1.5"
Usage
Set environment variables
Before running, set your Instagram login credentials (PowerShell example on Windows):

powershell
Copy code
$env:IG_USERNAME="your_instagram_username"
$env:IG_PASSWORD="your_instagram_password"
(Optional) set a custom session file path:

powershell
Copy code
$env:IG_SESSION_FILE="my_session.json"
Run the script
bash
Copy code
python instagram_insights_scraper.py username1 username2 username3
Example:

bash
Copy code
python instagram_insights_scraper.py all.american.eng englishwiththisguy eslkate
This will:

Fetch up to 25 recent posts per username

Analyze engagement & content features

Save <username>_summary.csv with a one-row profile summary

Example Output
Console Summary:

yaml
Copy code
===== @all.american.eng (Instagram) =====
Followers:              12,345
Following:              1,234
Analyzed posts:         25
Avg Likes:              1,234.56
Avg Comments:           78.90
View-adjusted ER:       mean=0.0456, median=0.0444
Post frequency:         2.34 posts/week
Content type:           Instagram (Post/Reel)
Content theme:          vocabulary
Avg shares / saves:     0.00 / 0.00
Country/Region:         United States
CSV Schema:

tiktok_profile_name (Instagram display name)

username

posts_analyzed

avg_likes, avg_comments, avg_shares, avg_saves

engagement_rate_view_adj_mean

post_frequency_per_week

content_type, content_theme, country_region

hashtags_used

hashtag_efficiency_top

posting_window_performance

caption_length_vs_er

content_category_lift_top

Notes
Instagram does not expose saves/shares publicly → these are always reported as 0.

For photos, “views” are typically not available, so view-adjusted ER applies mainly to Reels/Video.

Script is for educational and research purposes. Use responsibly and respect Instagram’s Terms of Service.

License
This project is open source under the MIT License.

yaml
Copy code

---

👉 Do you want me to also create a ready-to-use **`.gitignore`** file (to ignore session files, CSV outputs, a

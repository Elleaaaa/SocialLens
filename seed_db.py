"""Seed a fresh monitor.db with the 225 profiles (Facebook excluded).
Adds a nullable company column. Run once: python seed_db.py
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "monitor.db"

# Remove old db if exists (fresh start)
if DB_PATH.exists():
    DB_PATH.unlink()

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    company TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    post_id TEXT NOT NULL,
    title TEXT,
    url TEXT,
    detected_at TEXT NOT NULL,
    published_at TEXT,
    UNIQUE(profile_id, post_id)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    profile_id TEXT,
    platform TEXT,
    status TEXT,
    new_posts INTEGER DEFAULT 0,
    message TEXT
);

CREATE TABLE IF NOT EXISTS profile_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    followers INTEGER DEFAULT 0,
    captured_at TEXT NOT NULL
);
"""

PROFILES = [
    ("instagram", "ASHAD", "https://www.instagram.com/wellness.with.zay", "BUNDLE"),
    ("instagram", "ASHAD 1", "https://www.instagram.com/wellness.with.roman", "BUNDLE"),
    ("instagram", "EDWA", "https://www.instagram.com/health.zora", "BUNDLE"),
    ("instagram", "IMRAN", "https://www.instagram.com/hollywood.protocol", "BUNDLE"),
    ("instagram", "NASAR", "https://www.instagram.com/livelikenasir", "BUNDLE"),
    ("instagram", "BROOKE HOCKETT", "https://www.instagram.com/motherrosewellness", "BUNDLE"),
    ("instagram", "KIAI KAWAI", "https://www.instagram.com/therealmamasachi", "BUNDLE"),
    ("instagram", "MAL", "https://www.instagram.com/harmoncoastal", "BUNDLE"),
    ("instagram", "MIRANDA", "https://www.instagram.com/80yo_yogi", "BUNDLE"),
    ("tiktok", "STEFAN", "https://www.tiktok.com/@minjaehanmd", "BUNDLE"),
    ("instagram", "STEFAN 1", "https://www.instagram.com/thehanmethod", "BUNDLE"),
    ("instagram", "DK", "https://www.instagram.com/donovanasaju", "BUNDLE"),
    ("instagram", "PAOLO", "https://www.instagram.com/reel/DZ3RwCixoVM", "BUNDLE"),
    ("instagram", "WILLYBOY", "https://www.instagram.com/yoder.wellness", "BUNDLE"),
    ("instagram", "DYLAN H", "https://www.instagram.com/wellnesswithdemarcus", "BUNDLE"),
    ("threads", "DYLAN H 1", "https://www.threads.com/@wellnesswithdemarcus", "BUNDLE"),
    ("tiktok", "SIMONGP", "https://www.tiktok.com/@hamilton.ye", "BUNDLE"),
    ("instagram", "SIMONGP 1", "https://www.instagram.com/haniltonye", "BUNDLE"),
    ("tiktok", "CHRISTIANCLARK", "https://www.tiktok.com/@auntie.nia.health", "BUNDLE"),
    ("instagram", "CHRISTIANCLARK 1", "https://www.instagram.com/auntie.nia.health", "BUNDLE"),
    ("threads", "CHRISTIANCLARK 2", "https://www.threads.com/@auntie.nia.health", "BUNDLE"),
    ("tiktok", "ADAM", "https://www.tiktok.com/@naomi.wellness60", "BUNDLE"),
    ("instagram", "ADAM 1", "https://www.instagram.com/naomi.wellness60", "BUNDLE"),
    ("instagram", "MPMPMPMP", "https://www.instagram.com/wellnessgroveusa", "BUNDLE"),
    ("tiktok", "CRIS", "https://www.tiktok.com/@lee.sinmed", "BUNDLE"),
    ("tiktok", "CRIS 1", "https://www.tiktok.com/@sinchanmed", "BUNDLE"),
    ("instagram", "CRIS 2", "https://www.instagram.com/lee.sinmed", "BUNDLE"),
    ("instagram", "CRIS 3", "https://www.instagram.com/sinchan.med", "BUNDLE"),
    ("instagram", "EDUARDOD", "https://www.instagram.com/mike.navarro_", "BUNDLE"),
    ("instagram", "EDUARDOD 1", "https://www.instagram.com/earlinejones_", "BUNDLE"),
    ("instagram", "CALEB", "https://www.instagram.com/sawyer.educational", "SOURSOP"),
    ("instagram", "CALEB 1", "https://www.instagram.com/treyrockshealth", "SOURSOP"),
    ("instagram", "CALEB 2", "https://www.instagram.com/mybeautyhelping", "SOURSOP"),
    ("instagram", "CALEB 3", "https://www.instagram.com/neena.brinninghealth", "SOURSOP"),
    ("threads", "CALEB 4", "https://www.threads.net/@sawyer.educational", "SOURSOP"),
    ("threads", "CALEB 5", "https://www.threads.net/@patel_vikram29", "SOURSOP"),
    ("threads", "CALEB 6", "https://www.threads.com/@thompson_earl9", "SOURSOP"),
    ("youtube", "ROLAND", "https://www.youtube.com/@silas.traditionalmed", "SOURSOP"),
    ("instagram", "JACKRCAMPBELL", "https://www.instagram.com/vanessa.healthtips", "SOURSOP"),
    ("instagram", "JACKRCAMPBELL 1", "https://www.instagram.com/marcuselwood_", "SOURSOP"),
    ("instagram", "JACKRCAMPBELL 2", "https://www.instagram.com/damon.rivers.health", "SOURSOP"),
    ("threads", "JACKRCAMPBELL 3", "https://www.threads.com/@vanessa.healthtips", "SOURSOP"),
    ("instagram", "JACKSON", "https://www.instagram.com/wellnessnataliehorne", "SOURSOP"),
    ("instagram", "JACKSON 1", "https://www.instagram.com/drliangwei_wellness", "SOURSOP"),
    ("instagram", "OLLIE", "https://www.instagram.com/mother.satori", "SOURSOP"),
    ("threads", "OLLIE 1", "https://www.threads.com/@mother.satori", "SOURSOP"),
    ("tiktok", "SANES", "https://www.tiktok.com/@soy.mamaprimeriza", "SOURSOP"),
    ("tiktok", "SANES 1", "https://www.tiktok.com/@mamaprimeriza_3", "SOURSOP"),
    ("tiktok", "SANES 2", "https://www.tiktok.com/@belleza.moderna93", "SOURSOP"),
    ("instagram", "FRANK PALMERI", "https://www.instagram.com/shenmuhealer", "SOURSOP"),
    ("instagram", "FRANK PALMERI 1", "https://www.instagram.com/realmamahattie", "SOURSOP"),
    ("instagram", "FRANK PALMERI 2", "https://www.instagram.com/stevenchen.healings", "SOURSOP"),
    ("instagram", "TANKAS", "https://www.instagram.com/wellness.jada", "SOURSOP"),
    ("threads", "TANKAS 1", "https://www.threads.com/@wellness.jada", "SOURSOP"),
    ("youtube", "TANKAS 2", "https://www.youtube.com/@JadaWellness", "SOURSOP"),
    ("instagram", "JEFFREY", "https://www.instagram.com/lee.healthsecrets", "SOURSOP"),
    ("instagram", "JAKE", "https://www.instagram.com/healthtipsbyandre", "SOURSOP"),
    ("instagram", "BRADY RIFE", "https://www.instagram.com/zionheals", "SOURSOP"),
    ("instagram", "BRADY RIFE 1", "https://www.instagram.com/blakeheals", "SOURSOP"),
    ("instagram", "EVAN_40781", "https://www.instagram.com/shamanglenroy", "SOURSOP"),
    ("instagram", "NEEL", "https://www.instagram.com/kenjilifejapan", "SOURSOP"),
    ("instagram", "ZAK", "https://www.instagram.com/reggiehealth", "SOURSOP"),
    ("instagram", "JAMES", "https://www.instagram.com/healthwithleon", "SOURSOP"),
    ("instagram", "ALEXDEALZ", "https://www.instagram.com/misscecewellness", "SOURSOP"),
    ("instagram", "ALEXDEALZ 1", "https://www.instagram.com/malcolmheals", "SOURSOP"),
    ("instagram", "ALEXDEALZ 2", "https://www.instagram.com/blakethehealer", "SOURSOP"),
    ("instagram", "FRANKIEDEALS", "https://www.instagram.com/themindofjaxson", "SOURSOP"),
    ("instagram", "FRANKIEDEALS 1", "https://www.instagram.com/thereallucapenzo", "SOURSOP"),
    ("instagram", "FRANKIEDEALS 2", "https://www.instagram.com/marlonshealinghut", "SOURSOP"),
    ("instagram", "SPENCER", "https://www.instagram.com/wentaohealing", "SOURSOP"),
    ("instagram", "SPENCER 1", "https://www.instagram.com/heath.heals", "SOURSOP"),
    ("instagram", "SPENCER 2", "https://www.instagram.com/sedona.sarah", "SOURSOP"),
    ("instagram", "CONNOR", "https://www.instagram.com/charleston.healer", "SOURSOP"),
    ("instagram", "DYLEO1540", "https://www.instagram.com/knoxflint1948", "SOURSOP"),
    ("instagram", "DYLEO1540 1", "https://www.instagram.com/deandreflint1948", "SOURSOP"),
    ("instagram", "FARIZ DEMIRI", "https://www.instagram.com/lila_ananda_", "SOURSOP"),
    ("instagram", "FARIZ DEMIRI 1", "https://www.instagram.com/calebstonefarms", "SOURSOP"),
    ("instagram", "FARIZ DEMIRI 2", "https://www.instagram.com/amish.health.secrets", "SOURSOP"),
    ("threads", "FARIZ DEMIRI 3", "https://www.threads.com/@lila_ananda_", "SOURSOP"),
    ("youtube", "FARIZ DEMIRI 4", "https://www.youtube.com/@LilaAnandaHealth", "SOURSOP"),
    ("x", "FARIZ DEMIRI 5", "https://x.com/LilaAnanda_", "SOURSOP"),
    ("instagram", "NATHANR", "https://www.instagram.com/healthtimedaily", "SOURSOP"),
    ("threads", "NATHANR 1", "https://www.threads.com/@healthtimedaily", "SOURSOP"),
    ("instagram", "MILES", "https://www.instagram.com/robertparkwellness", "SOURSOP"),
    ("instagram", "ZAINE", "https://www.instagram.com/healthwithmatt", "SOURSOP"),
    ("instagram", "ZAINE 1", "https://www.instagram.com/wellness_myles", "SOURSOP"),
    ("instagram", "ALEJANDRO", "https://www.instagram.com/rochelllecarter", "SOURSOP"),
    ("instagram", "ALEJANDRO 1", "https://www.instagram.com/rubby.health", "SOURSOP"),
    ("instagram", "SWANS", "https://www.instagram.com/malikhealing", "SOURSOP"),
    ("instagram", "SAM GUZMANN", "https://www.instagram.com/zafarheals", "SOURSOP"),
    ("instagram", "MATT G", "https://www.instagram.com/marcuswebbwellness", "SOURSOP"),
    ("instagram", "LUKAS HUMHAL", "https://www.instagram.com/allens.health", "SOURSOP"),
    ("instagram", "LUKAS HUMHAL 1", "https://www.instagram.com/davids.health", "SOURSOP"),
    ("threads", "LUKAS HUMHAL 2", "https://www.threads.com/@allens.health", "SOURSOP"),
    ("instagram", "EMIL", "https://www.instagram.com/thecoltmercer", "SOURSOP"),
    ("instagram", "EMIL 1", "https://www.instagram.com/chen.medicina.china", "SOURSOP"),
    ("tiktok", "PETRIKSVETRIK", "https://www.tiktok.com/@papa.elias.roots", "SOURSOP"),
    ("instagram", "PETRIKSVETRIK 1", "https://www.instagram.com/papa.elias.roots", "SOURSOP"),
    ("instagram", "TIM", "https://www.instagram.com/thenativeways", "SOURSOP"),
    ("instagram", "TIM 1", "https://www.instagram.com/ross.chinesehealth", "SOURSOP"),
    ("instagram", "DUCANH", "https://www.instagram.com/sefubrownlife", "SOURSOP"),
    ("instagram", "$TAE", "https://www.instagram.com/elderaro", "SOURSOP"),
    ("threads", "$TAE 1", "https://www.threads.com/@elderaro", "SOURSOP"),
    ("instagram", "MILESXC", "https://www.instagram.com/jalenheals", "SOURSOP"),
    ("instagram", "BEN C", "https://www.instagram.com/kofithehealer", "SOURSOP"),
    ("instagram", "MUKTHAR", "https://www.instagram.com/livelikerafael", "SOURSOP"),
    ("instagram", "MUKTHAR 1", "https://www.instagram.com/caidenheals", "SOURSOP"),
    ("instagram", "MUKTHAR 2", "https://www.instagram.com/livelikemarcus", "SOURSOP"),
    ("instagram", "OSHER", "https://www.instagram.com/granthealing", "SOURSOP"),
    ("instagram", "OSHER 1", "https://www.instagram.com/sophia.healing", "SOURSOP"),
    ("instagram", "OSHER 2", "https://www.instagram.com/blakehealing", "SOURSOP"),
    ("tiktok", "KAEMON", "https://www.tiktok.com/@shannon.rose55", "SOURSOP"),
    ("instagram", "KAEMON 1", "https://www.instagram.com/shannonremedies", "SOURSOP"),
    ("youtube", "KAEMON 2", "https://youtube.com/@shannonrose.55", "SOURSOP"),
    ("instagram", "ROMA", "https://www.instagram.com/rasnathaniel", "SOURSOP"),
    ("instagram", "LAYLINE", "https://www.instagram.com/sarahwellnessdaily", "SOURSOP"),
    ("instagram", "LAYLINE 1", "https://www.instagram.com/masterlin.balance", "SOURSOP"),
    ("instagram", "LAYLINE 2", "https://www.instagram.com/jessicawellness_", "SOURSOP"),
    ("instagram", "LAYLINE 3", "https://www.instagram.com/johncarter.health", "SOURSOP"),
    ("youtube", "LAYLINE 4", "https://www.youtube.com/@johncarterhealth", "SOURSOP"),
    ("youtube", "LAYLINE 5", "https://www.youtube.com/@sarahwellnessdaily", "SOURSOP"),
    ("youtube", "LAYLINE 6", "https://www.youtube.com/channel/UCdaYLTnZ9XPlH-kAWgTQJaw", "SOURSOP"),
    ("youtube", "LAYLINE 7", "https://www.youtube.com/@thejessicawellness", "SOURSOP"),
    ("instagram", "HEATHERMARGARET", "https://www.instagram.com/hoffmanhealth1", "SOURSOP"),
    ("instagram", "HEATHERMARGARET 1", "https://www.instagram.com/patel_vikram29", "SOURSOP"),
    ("threads", "HEATHERMARGARET 2", "https://www.threads.com/@patel_vikram29", "SOURSOP"),
    ("instagram", "ANEESH", "https://www.instagram.com/granthealthtips", "SOURSOP"),
    ("instagram", "ANEESH 1", "https://www.instagram.com/womenshealth.tips", "SOURSOP"),
    ("instagram", "2MS", "https://www.instagram.com/ray.quackenbush", "SOURSOP"),
    ("tiktok", "PALDO", "https://www.tiktok.com/@nowellswellness", "SOURSOP"),
    ("instagram", "PALDO 1", "https://www.instagram.com/wellnesssecrettips", "SOURSOP"),
    ("instagram", "PALDO 2", "https://www.instagram.com/secretwellnesstips_365", "SOURSOP"),
    ("threads", "PALDO 3", "https://www.threads.com/@wellnesssecrettips", "SOURSOP"),
    ("threads", "PALDO 4", "https://www.threads.com/@secretwellnesstips_365", "SOURSOP"),
    ("instagram", "MR. IMPROVE", "https://www.instagram.com/bennett.health", "SOURSOP"),
    ("threads", "MR. IMPROVE 1", "https://www.threads.com/@michael_reynolds_health ", "SOURSOP"),
    ("instagram", "ALEKS4443", "https://www.instagram.com/deansadventures2", "SOURSOP"),
    ("instagram", "ALEKS4443 1", "https://www.instagram.com/wellnessfatu", "SOURSOP"),
    ("instagram", "TYLER METE", "https://www.instagram.com/eternallinshenyi", "SOURSOP"),
    ("instagram", "TAJ", "https://www.instagram.com/mateo.wellness", "SOURSOP"),
    ("instagram", "JOUSMITH", "https://www.instagram.com/motherpearlheals", "SOURSOP"),
    ("instagram", "ZAYN", "https://www.instagram.com/adrian_zale_", "SOURSOP"),
    ("instagram", "PETERLEJA", "https://www.instagram.com/vnicolebrooks", "SOURSOP"),
    ("instagram", "PETERLEJA 1", "https://www.instagram.com/amirihealingtips", "SOURSOP"),
    ("instagram", "PETERLEJA 2", "https://www.instagram.com/vanessaca1ter", "SOURSOP"),
    ("threads", "PETERLEJA 3", "https://www.threads.com/@amirihealingtips", "SOURSOP"),
    ("instagram", "OZ", "https://www.instagram.com/alexobrianssh", "SOURSOP"),
    ("instagram", "MARTHA", "https://www.instagram.com/unclekofiherbalist", "SOURSOP"),
    ("instagram", "GLENN REEVES", "https://www.instagram.com/reel/Da3kO2TRg4i", "SOURSOP"),
    ("instagram", "FRANCO", "https://www.instagram.com/eli.wagner.wellness", "SOURSOP"),
    ("tiktok", "LEDAVID", "https://www.tiktok.com/@richlikejulian", "SOURSOP"),
    ("instagram", "LEDAVID 1", "https://www.instagram.com/richlikejulian", "SOURSOP"),
    ("tiktok", "MARWAN", "https://www.tiktok.com/@wellness.marva", "SOURSOP"),
    ("instagram", "MARWAN 1", "https://www.instagram.com/mamamarvaus", "SOURSOP"),
    ("threads", "MARWAN 2", "https://www.threads.com/@mamamarvaus", "SOURSOP"),
    ("youtube", "MARWAN 3", "https://youtube.com/@mamamarvaa", "SOURSOP"),
    ("instagram", "YOGYRT", "https://www.instagram.com/apollo_wellness_", "SOURSOP"),
    ("instagram", "RYWU", "https://www.instagram.com/mayasaferi", "SOURSOP"),
    ("instagram", "RYWU 1", "https://www.instagram.com/mamasageremedies", "SOURSOP"),
    ("instagram", "RYWU 2", "https://www.instagram.com/kaikoreanmed", "SOURSOP"),
    ("tiktok", "MR. DAN", "https://www.tiktok.com/@holistic_aura_", "SOURSOP"),
    ("tiktok", "MR. DAN 1", "https://www.tiktok.com/@naturalive0", "SOURSOP"),
    ("tiktok", "MR. DAN 2", "https://www.tiktok.com/@healthy_lifee01", "SOURSOP"),
    ("instagram", "MR. DAN 3", "https://www.instagram.com/holistic_aura_", "SOURSOP"),
    ("threads", "MR. DAN 4", "https://www.threads.com/@healthy_lifeee01", "SOURSOP"),
    ("instagram", "RSN & PARTNERS", "https://www.instagram.com/maxhealingwithlan", "SOURSOP"),
    ("instagram", "RSN & PARTNERS 1", "https://www.instagram.com/ongombojuleson", "SOURSOP"),
    ("instagram", "RSN & PARTNERS 2", "https://www.instagram.com/healingwithmaggle", "SOURSOP"),
    ("instagram", "ABEL", "https://www.instagram.com/akira.kenko", "SOURSOP"),
    ("threads", "ABEL 1", "https://www.threads.com/@akira.kenko", "SOURSOP"),
    ("tiktok", "EDUARDO", "https://www.tiktok.com/@the.gut.guru4", "SOURSOP"),
    ("instagram", "EDUARDO 1", "https://www.instagram.com/thegutguru00", "SOURSOP"),
    ("threads", "EDUARDO 2", "https://www.threads.com/@thegutguru00", "SOURSOP"),
    ("youtube", "EDUARDO 3", "https://www.youtube.com/@thegutguru", "SOURSOP"),
    ("instagram", "RASHID", "https://www.instagram.com/elijahhealthtipss", "SOURSOP"),
    ("instagram", "MARTIN R", "https://www.instagram.com/danlleemd", "SOURSOP"),
    ("tiktok", "RUBEN", "https://www.tiktok.com/@wellness.withjames", "SOURSOP"),
    ("instagram", "RUBEN 1", "https://www.instagram.com/wellness.withjames", "SOURSOP"),
    ("instagram", "TY", "https://www.instagram.com/farmertommy3", "SOURSOP"),
    ("instagram", "HAYDEN", "https://www.instagram.com/marcus.wellnesssuite", "SOURSOP"),
    ("instagram", "HAYDEN 1", "https://www.instagram.com/xaviercolewellness", "SOURSOP"),
    ("instagram", "TEKO", "https://www.instagram.com/darnellwellness", "SOURSOP"),
    ("instagram", "TEKO 1", "https://www.instagram.com/roymcwellness", "SOURSOP"),
    ("instagram", "TEKO 2", "https://www.instagram.com/robwellnesstips", "SOURSOP"),
    ("instagram", "TEKO 3", "https://www.instagram.com/clarkswellnesstips", "SOURSOP"),
    ("instagram", "TEKO 4", "https://www.instagram.com/colbywellnesstips", "SOURSOP"),
    ("tiktok", "MUMIN", "https://www.tiktok.com/@nightswithadam", "SOURSOP"),
    ("instagram", "MUMIN 1", "https://www.instagram.com/nightswithadam", "SOURSOP"),
    ("youtube", "MUMIN 2", "https://www.youtube.com/@nightswithadam", "SOURSOP"),
    ("instagram", "PRINCE", "https://www.instagram.com/imamarasoleil", "SOURSOP"),
    ("instagram", "PRINCE 1", "https://www.instagram.com/imryanmercer", "SOURSOP"),
    ("instagram", "BRIAN", "https://www.instagram.com/leonshealths", "SOURSOP"),
    ("tiktok", "BAE", "https://www.tiktok.com/@thedaoren", "SOURSOP"),
    ("instagram", "BAE 1", "https://www.instagram.com/imdaoren", "SOURSOP"),
    ("tiktok", "MUSABSPORZINGIS", "https://www.tiktok.com/@alohatohealing", "SOURSOP"),
    ("instagram", "A TRAIN", "https://www.instagram.com/oakwoodvitality", "SOURSOP"),
    ("instagram", "CARTER", "https://www.instagram.com/wyoming.buck", "SOURSOP"),
    ("tiktok", "JAY ALEJANDRO", "https://www.tiktok.com/@winston.knowles", "SOURSOP"),
    ("instagram", "JAY ALEJANDRO 1", "https://www.instagram.com/winston.knowles", "SOURSOP"),
    ("instagram", "TINO", "https://www.instagram.com/mama.amina.wellness", "SOURSOP"),
    ("instagram", "MAXBEAR", "https://www.instagram.com/health.withkyro", "SOURSOP"),
    ("instagram", "KELLAN", "https://www.instagram.com/wellnessfromdaniel", "SOURSOP"),
    ("instagram", "KELLAN 1", "https://www.instagram.com/malikwellness1", "SOURSOP"),
    ("tiktok", "ANISH", "https://www.tiktok.com/@kaizo_ikigai", "SOURSOP"),
    ("instagram", "ANISH 1", "https://www.instagram.com/kaizo_ikigai", "SOURSOP"),
    ("threads", "ANISH 2", "https://www.threads.com/@kaizo_ikigai", "SOURSOP"),
    ("youtube", "ANISH 3", "https://www.youtube.com/@ikigai_health", "SOURSOP"),
    ("instagram", "PATRICK", "https://www.instagram.com/wendyswellnesscenter", "SOURSOP"),
    ("threads", "PATRICK 1", "https://www.threads.com/@wendyswellnesscenter", "SOURSOP"),
    ("tiktok", "WAYNE C", "https://www.tiktok.com/@sterlingweikong", "SOURSOP"),
    ("tiktok", "WAYNE C 1", "https://www.tiktok.com/@uncle.luka.wisdom", "SOURSOP"),
    ("instagram", "WAYNE C 2", "https://www.instagram.com/sterlingweikong", "SOURSOP"),
    ("instagram", "WAYNE C 3", "https://www.instagram.com/uncle.luka.wisdom", "SOURSOP"),
    ("threads", "WAYNE C 4", "https://www.threads.com/@sterlingweikong", "SOURSOP"),
    ("threads", "WAYNE C 5", "https://www.threads.com/@uncle.luka.wisdom", "SOURSOP"),
    ("youtube", "WAYNE C 6", "https://www.youtube.com/@SterlingWeiKong", "SOURSOP"),
    ("youtube", "WAYNE C 7", "https://www.youtube.com/@uncleluka-x5c", "SOURSOP"),
    ("youtube", "PHIL", "https://www.youtube.com/channel/UCNPf2K5dMOe0Oon3EcFfzbQ", "SOURSOP"),
    ("tiktok", "JP", "https://www.tiktok.com/@vincentheals", "SOURSOP"),
]


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    for i, (platform, name, url, company) in enumerate(PROFILES, 1):
        # ID format: {platform_prefix}-{index}-{name_prefix}
        name_prefix = name[:3].lower().replace(" ", "")
        profile_id = f"{platform[:2]}-{i}-{name_prefix}"
        conn.execute(
            "INSERT INTO profiles (id, platform, name, url, company) VALUES (?, ?, ?, ?, ?)",
            (profile_id, platform, name, url, company),
        )

    conn.commit()

    count = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
    print(f"Created fresh monitor.db with {count} profiles")
    print("(posts, runs, profile_metrics tables are empty)")

    # Show summary
    for row in conn.execute(
        "SELECT platform, COUNT(*) as c FROM profiles GROUP BY platform ORDER BY platform"
    ):
        print(f"  {row['platform']}: {row['c']}")

    conn.close()


if __name__ == "__main__":
    main()

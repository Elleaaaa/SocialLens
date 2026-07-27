"""Seed a fresh monitor.db with the 225 profiles (Facebook excluded).
Run once: python seed_db.py
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
    ("instagram", "CALEB", "https://www.instagram.com/sawyer.educational"),
    ("instagram", "CALEB 1", "https://www.instagram.com/treyrockshealth"),
    ("instagram", "CALEB 2", "https://www.instagram.com/mybeautyhelping"),
    ("instagram", "CALEB 3", "https://www.instagram.com/neena.brinninghealth"),
    ("threads", "CALEB 4", "https://www.threads.net/@sawyer.educational"),
    ("threads", "CALEB 5", "https://www.threads.net/@patel_vikram29"),
    ("threads", "CALEB 6", "https://www.threads.com/@thompson_earl9"),
    ("youtube", "ROLAND", "https://www.youtube.com/@silas.traditionalmed"),
    ("instagram", "JACKRCAMPBELL", "https://www.instagram.com/vanessa.healthtips"),
    ("instagram", "JACKRCAMPBELL 1", "https://www.instagram.com/marcuselwood_"),
    ("instagram", "JACKRCAMPBELL 2", "https://www.instagram.com/damon.rivers.health"),
    ("threads", "JACKRCAMPBELL 3", "https://www.threads.com/@vanessa.healthtips"),
    ("instagram", "JACKSON", "https://www.instagram.com/wellnessnataliehorne"),
    ("instagram", "JACKSON 1", "https://www.instagram.com/drliangwei_wellness"),
    ("instagram", "OLLIE", "https://www.instagram.com/mother.satori"),
    ("threads", "OLLIE 1", "https://www.threads.com/@mother.satori"),
    ("tiktok", "SANES", "https://www.tiktok.com/@soy.mamaprimeriza"),
    ("tiktok", "SANES 1", "https://www.tiktok.com/@mamaprimeriza_3"),
    ("tiktok", "SANES 2", "https://www.tiktok.com/@belleza.moderna93"),
    ("instagram", "FRANK PALMERI", "https://www.instagram.com/shenmuhealer"),
    ("instagram", "FRANK PALMERI 1", "https://www.instagram.com/realmamahattie"),
    ("instagram", "FRANK PALMERI 2", "https://www.instagram.com/stevenchen.healings"),
    ("instagram", "TANKAS", "https://www.instagram.com/wellness.jada"),
    ("threads", "TANKAS 1", "https://www.threads.com/@wellness.jada"),
    ("youtube", "TANKAS 2", "https://www.youtube.com/@JadaWellness"),
    ("instagram", "JEFFREY", "https://www.instagram.com/lee.healthsecrets"),
    ("instagram", "JAKE", "https://www.instagram.com/healthtipsbyandre"),
    ("youtube", "LEO", "https://www.youtube.com/@Wellness.Camille"),
    ("lemon8", "LEO 1", "https://www.lemon8-app.com/@healthsecrets365"),
    ("instagram", "BRADY RIFE", "https://www.instagram.com/zionheals"),
    ("instagram", "BRADY RIFE 1", "https://www.instagram.com/blakeheals"),
    ("instagram", "SCOTT", "https://www.instagram.com/janet.knows.herbs"),
    ("instagram", "EVAN_40781", "https://www.instagram.com/shamanglenroy"),
    ("instagram", "NEEL", "https://www.instagram.com/kenjilifejapan"),
    ("instagram", "ZAK", "https://www.instagram.com/reggiehealth"),
    ("instagram", "JAMES", "https://www.instagram.com/healthwithleon"),
    ("instagram", "ALEXDEALZ", "https://www.instagram.com/misscecewellness"),
    ("instagram", "ALEXDEALZ 1", "https://www.instagram.com/malcolmheals"),
    ("instagram", "ALEXDEALZ 2", "https://www.instagram.com/blakethehealer"),
    ("instagram", "FRANKIEDEALS", "https://www.instagram.com/themindofjaxson"),
    ("instagram", "FRANKIEDEALS 1", "https://www.instagram.com/thereallucapenzo"),
    ("instagram", "FRANKIEDEALS 2", "https://www.instagram.com/marlonshealinghut"),
    ("instagram", "SPENCER", "https://www.instagram.com/wentaohealing"),
    ("instagram", "SPENCER 1", "https://www.instagram.com/heath.heals"),
    ("instagram", "SPENCER 2", "https://www.instagram.com/sedona.sarah"),
    ("instagram", "CONNOR", "https://www.instagram.com/charleston.healer"),
    ("instagram", "DYLEO1540", "https://www.instagram.com/knoxflint1948"),
    ("instagram", "DYLEO1540 1", "https://www.instagram.com/deandreflint1948"),
    ("instagram", "FARIZ DEMIRI", "https://www.instagram.com/lila_ananda_"),
    ("instagram", "FARIZ DEMIRI 1", "https://www.instagram.com/calebstonefarms"),
    ("instagram", "FARIZ DEMIRI 2", "https://www.instagram.com/amish.health.secrets"),
    ("threads", "FARIZ DEMIRI 3", "https://www.threads.com/@lila_ananda_"),
    ("youtube", "FARIZ DEMIRI 4", "https://www.youtube.com/@LilaAnandaHealth"),
    ("x", "FARIZ DEMIRI 5", "https://x.com/LilaAnanda_"),
    ("instagram", "NATHANR", "https://www.instagram.com/healthtimedaily"),
    ("threads", "NATHANR 1", "https://www.threads.com/@healthtimedaily"),
    ("instagram", "MILES", "https://www.instagram.com/robertparkwellness"),
    ("instagram", "ZAINE", "https://www.instagram.com/healthwithmatt"),
    ("instagram", "ZAINE 1", "https://www.instagram.com/wellness_myles"),
    ("instagram", "ALEJANDRO", "https://www.instagram.com/rochelllecarter"),
    ("instagram", "ALEJANDRO 1", "https://www.instagram.com/rubby.health"),
    ("instagram", "SWANS", "https://www.instagram.com/malikhealing"),
    ("instagram", "SAM GUZMANN", "https://www.instagram.com/zafarheals"),
    ("instagram", "MATT G", "https://www.instagram.com/marcuswebbwellness"),
    ("instagram", "LUKAS HUMHAL", "https://www.instagram.com/allens.health"),
    ("instagram", "LUKAS HUMHAL 1", "https://www.instagram.com/davids.health"),
    ("threads", "LUKAS HUMHAL 2", "https://www.threads.com/@allens.health"),
    ("instagram", "EMIL", "https://www.instagram.com/thecoltmercer"),
    ("instagram", "EMIL 1", "https://www.instagram.com/chen.medicina.china"),
    ("tiktok", "PETRIKSVETRIK", "https://www.tiktok.com/@papa.elias.roots"),
    ("instagram", "PETRIKSVETRIK 1", "https://www.instagram.com/papa.elias.roots"),
    ("instagram", "TIM", "https://www.instagram.com/thenativeways"),
    ("instagram", "TIM 1", "https://www.instagram.com/ross.chinesehealth"),
    ("instagram", "DUCANH", "https://www.instagram.com/sefubrownlife"),
    ("instagram", "JIMMA", "https://www.instagram.com/zhen.po.wellness"),
    ("instagram", "$TAE", "https://www.instagram.com/elderaro"),
    ("threads", "$TAE 1", "https://www.threads.com/@elderaro"),
    ("instagram", "MILESXC", "https://www.instagram.com/jalenheals"),
    ("instagram", "BEN C", "https://www.instagram.com/kofithehealer"),
    ("instagram", "MUKTHAR", "https://www.instagram.com/livelikerafael"),
    ("instagram", "MUKTHAR 1", "https://www.instagram.com/caidenheals"),
    ("instagram", "MUKTHAR 2", "https://www.instagram.com/livelikemarcus"),
    ("instagram", "OSHER", "https://www.instagram.com/granthealing"),
    ("instagram", "OSHER 1", "https://www.instagram.com/sophia.healing"),
    ("instagram", "OSHER 2", "https://www.instagram.com/blakehealing"),
    ("tiktok", "KAEMON", "https://www.tiktok.com/@shannon.rose55"),
    ("instagram", "KAEMON 1", "https://www.instagram.com/shannonremedies"),
    ("youtube", "KAEMON 2", "https://youtube.com/@shannonrose.55"),
    ("instagram", "ROMA", "https://www.instagram.com/rasnathaniel"),
    ("instagram", "LAYLINE", "https://www.instagram.com/sarahwellnessdaily"),
    ("instagram", "LAYLINE 1", "https://www.instagram.com/masterlin.balance"),
    ("instagram", "LAYLINE 2", "https://www.instagram.com/jessicawellness_"),
    ("instagram", "LAYLINE 3", "https://www.instagram.com/johncarter.health"),
    ("youtube", "LAYLINE 4", "https://www.youtube.com/@johncarterhealth"),
    ("youtube", "LAYLINE 5", "https://www.youtube.com/@sarahwellnessdaily"),
    (
        "youtube",
        "LAYLINE 6",
        "https://www.youtube.com/channel/UCdaYLTnZ9XPlH-kAWgTQJaw",
    ),
    ("youtube", "LAYLINE 7", "https://www.youtube.com/@thejessicawellness"),
    ("instagram", "HEATHERMARGARET", "https://www.instagram.com/hoffmanhealth1"),
    ("instagram", "HEATHERMARGARET 1", "https://www.instagram.com/patel_vikram29"),
    ("threads", "HEATHERMARGARET 2", "https://www.threads.com/@patel_vikram29"),
    ("instagram", "ANEESH", "https://www.instagram.com/granthealthtips"),
    ("instagram", "ANEESH 1", "https://www.instagram.com/womenshealth.tips"),
    ("instagram", "2MS", "https://www.instagram.com/ray.quackenbush"),
    ("tiktok", "PALDO", "https://www.tiktok.com/@nowellswellness"),
    ("instagram", "PALDO 1", "https://www.instagram.com/wellnesssecrettips"),
    ("instagram", "PALDO 2", "https://www.instagram.com/secretwellnesstips_365"),
    ("threads", "PALDO 3", "https://www.threads.com/@wellnesssecrettips"),
    ("threads", "PALDO 4", "https://www.threads.com/@secretwellnesstips_365"),
    ("instagram", "MR. IMPROVE", "https://www.instagram.com/bennett.health"),
    ("threads", "MR. IMPROVE 1", "https://www.threads.com/@michael_reynolds_health /"),
    ("instagram", "ALEKS4443", "https://www.instagram.com/deansadventures2"),
    ("instagram", "ALEKS4443 1", "https://www.instagram.com/wellnessfatu"),
    ("instagram", "TYLER METE", "https://www.instagram.com/eternallinshenyi"),
    ("instagram", "TAJ", "https://www.instagram.com/mateo.wellness"),
    ("instagram", "JOUSMITH", "https://www.instagram.com/motherpearlheals"),
    ("instagram", "ZAYN", "https://www.instagram.com/adrian_zale_"),
    ("instagram", "PETERLEJA", "https://www.instagram.com/vnicolebrooks"),
    ("instagram", "PETERLEJA 1", "https://www.instagram.com/amirihealingtips"),
    ("instagram", "PETERLEJA 2", "https://www.instagram.com/vanessaca1ter"),
    ("threads", "PETERLEJA 3", "https://www.threads.com/@amirihealingtips"),
    ("instagram", "OZ", "https://www.instagram.com/alexobrianssh"),
    ("instagram", "MARTHA", "https://www.instagram.com/unclekofiherbalist"),
    ("instagram", "GLENN REEVES", "https://www.instagram.com/reel/Da3kO2TRg4i"),
    ("instagram", "FRANCO", "https://www.instagram.com/eli.wagner.wellness"),
    ("tiktok", "LEDAVID", "https://www.tiktok.com/@richlikejulian"),
    ("instagram", "LEDAVID 1", "https://www.instagram.com/richlikejulian"),
    ("tiktok", "MARWAN", "https://www.tiktok.com/@wellness.marva"),
    ("instagram", "MARWAN 1", "https://www.instagram.com/mamamarvaus"),
    ("threads", "MARWAN 2", "https://www.threads.com/@mamamarvaus"),
    ("youtube", "MARWAN 3", "https://youtube.com/@mamamarvaa"),
    ("instagram", "YOGYRT", "https://www.instagram.com/apollo_wellness_"),
    ("instagram", "RYWU", "https://www.instagram.com/mayasaferi"),
    ("instagram", "RYWU 1", "https://www.instagram.com/mamasageremedies"),
    ("instagram", "RYWU 2", "https://www.instagram.com/kaikoreanmed"),
    ("tiktok", "MR. DAN", "https://www.tiktok.com/@holistic_aura_"),
    ("tiktok", "MR. DAN 1", "https://www.tiktok.com/@naturalive0"),
    ("tiktok", "MR. DAN 2", "https://www.tiktok.com/@healthy_lifee01"),
    ("instagram", "MR. DAN 3", "https://www.instagram.com/holistic_aura_"),
    ("threads", "MR. DAN 4", "https://www.threads.com/@healthy_lifeee01"),
    ("instagram", "RSN & PARTNERS", "https://www.instagram.com/maxhealingwithlan"),
    ("instagram", "RSN & PARTNERS 1", "https://www.instagram.com/ongombojuleson"),
    ("instagram", "RSN & PARTNERS 2", "https://www.instagram.com/healingwithmaggle"),
    ("instagram", "ABEL", "https://www.instagram.com/akira.kenko"),
    ("threads", "ABEL 1", "https://www.threads.com/@akira.kenko"),
    ("tiktok", "EDUARDO", "https://www.tiktok.com/@the.gut.guru4"),
    ("instagram", "EDUARDO 1", "https://www.instagram.com/thegutguru00"),
    ("threads", "EDUARDO 2", "https://www.threads.com/@thegutguru00"),
    ("youtube", "EDUARDO 3", "https://www.youtube.com/@thegutguru"),
    ("instagram", "RASHID", "https://www.instagram.com/elijahhealthtipss"),
    ("instagram", "MARTIN R", "https://www.instagram.com/danlleemd"),
    ("tiktok", "RUBEN", "https://www.tiktok.com/@wellness.withjames"),
    ("instagram", "RUBEN 1", "https://www.instagram.com/wellness.withjames"),
    ("instagram", "TY", "https://www.instagram.com/farmertommy3"),
    ("instagram", "HAYDEN", "https://www.instagram.com/marcus.wellnesssuite"),
    ("instagram", "HAYDEN 1", "https://www.instagram.com/xaviercolewellness"),
    ("instagram", "TEKO", "https://www.instagram.com/darnellwellness"),
    ("instagram", "TEKO 1", "https://www.instagram.com/roymcwellness"),
    ("instagram", "TEKO 2", "https://www.instagram.com/robwellnesstips"),
    ("instagram", "TEKO 3", "https://www.instagram.com/clarkswellnesstips"),
    ("instagram", "TEKO 4", "https://www.instagram.com/colbywellnesstips"),
    ("tiktok", "MUMIN", "https://www.tiktok.com/@nightswithadam"),
    ("instagram", "MUMIN 1", "https://www.instagram.com/nightswithadam"),
    ("youtube", "MUMIN 2", "https://www.youtube.com/@nightswithadam"),
    ("instagram", "PRINCE", "https://www.instagram.com/imamarasoleil"),
    ("instagram", "PRINCE 1", "https://www.instagram.com/imryanmercer"),
    ("instagram", "BRIAN", "https://www.instagram.com/leonshealths"),
    ("tiktok", "BAE", "https://www.tiktok.com/@thedaoren"),
    ("instagram", "BAE 1", "https://www.instagram.com/imdaoren"),
    ("tiktok", "MUSABSPORZINGIS", "https://www.tiktok.com/@alohatohealing"),
    ("instagram", "A TRAIN", "https://www.instagram.com/oakwoodvitality"),
    ("instagram", "CARTER", "https://www.instagram.com/wyoming.buck"),
    ("tiktok", "JAY ALEJANDRO", "https://www.tiktok.com/@winston.knowles"),
    ("instagram", "JAY ALEJANDRO 1", "https://www.instagram.com/winston.knowles"),
    ("instagram", "TINO", "https://www.instagram.com/mama.amina.wellness"),
    ("instagram", "MAXBEAR", "https://www.instagram.com/health.withkyro"),
    ("youtube", "PREYZ", "https://www.youtube.com/@LongLifeGia"),
    ("youtube", "PREYZ 1", "https://www.youtube.com/@LongLifeHarumi"),
    ("instagram", "KELLAN", "https://www.instagram.com/wellnessfromdaniel"),
    ("instagram", "KELLAN 1", "https://www.instagram.com/malikwellness1"),
    ("tiktok", "ANISH", "https://www.tiktok.com/@kaizo_ikigai"),
    ("instagram", "ANISH 1", "https://www.instagram.com/kaizo_ikigai"),
    ("threads", "ANISH 2", "https://www.threads.com/@kaizo_ikigai"),
    ("youtube", "ANISH 3", "https://www.youtube.com/@ikigai_health"),
    ("instagram", "PATRICK", "https://www.instagram.com/wendyswellnesscenter"),
    ("threads", "PATRICK 1", "https://www.threads.com/@wendyswellnesscenter"),
    ("tiktok", "WAYNE C", "https://www.tiktok.com/@sterlingweikong"),
    ("tiktok", "WAYNE C 1", "https://www.tiktok.com/@uncle.luka.wisdom"),
    ("instagram", "WAYNE C 2", "https://www.instagram.com/sterlingweikong"),
    ("instagram", "WAYNE C 3", "https://www.instagram.com/uncle.luka.wisdom"),
    ("threads", "WAYNE C 4", "https://www.threads.com/@sterlingweikong"),
    ("threads", "WAYNE C 5", "https://www.threads.com/@uncle.luka.wisdom"),
    ("youtube", "WAYNE C 6", "https://www.youtube.com/@SterlingWeiKong"),
    ("youtube", "WAYNE C 7", "https://www.youtube.com/@uncleluka-x5c"),
    ("youtube", "PHIL", "https://www.youtube.com/channel/UCNPf2K5dMOe0Oon3EcFfzbQ"),
    ("tiktok", "JP", "https://www.tiktok.com/@vincentheals"),
    ("instagram", "ASHAD", "https://www.instagram.com/wellness.with.zay"),
    ("instagram", "ASHAD 1", "https://www.instagram.com/wellness.with.roman"),
    ("instagram", "EDWA", "https://www.instagram.com/health.zora"),
    ("instagram", "IMRAN", "https://www.instagram.com/hollywood.protocol"),
    ("instagram", "NASAR", "https://www.instagram.com/livelikenasir"),
    ("instagram", "BROOKE HOCKETT", "https://www.instagram.com/motherrosewellness"),
    ("instagram", "KIAI KAWAI", "https://www.instagram.com/therealmamasachi"),
    ("instagram", "MAL", "https://www.instagram.com/harmoncoastal"),
    ("instagram", "MIRANDA", "https://www.instagram.com/80yo_yogi"),
    ("tiktok", "STEFAN", "https://www.tiktok.com/@minjaehanmd"),
    ("instagram", "STEFAN 1", "https://www.instagram.com/thehanmethod"),
    ("instagram", "DK", "https://www.instagram.com/donovanasaju"),
    ("instagram", "PAOLO", "https://www.instagram.com/reel/DZ3RwCixoVM"),
    ("instagram", "WILLYBOY", "https://www.instagram.com/yoder.wellness"),
    ("instagram", "DYLAN H", "https://www.instagram.com/wellnesswithdemarcus"),
    ("threads", "DYLAN H 1", "https://www.threads.com/@wellnesswithdemarcus"),
    ("tiktok", "SIMONGP", "https://www.tiktok.com/@hamilton.ye"),
    ("instagram", "SIMONGP 1", "https://www.instagram.com/haniltonye"),
    ("tiktok", "CHRISTIANCLARK", "https://www.tiktok.com/@auntie.nia.health"),
    ("instagram", "CHRISTIANCLARK 1", "https://www.instagram.com/auntie.nia.health"),
    ("threads", "CHRISTIANCLARK 2", "https://www.threads.com/@auntie.nia.health"),
    ("tiktok", "ADAM", "https://www.tiktok.com/@naomi.wellness60"),
    ("instagram", "ADAM 1", "https://www.instagram.com/naomi.wellness60"),
    ("instagram", "MPMPMPMP", "https://www.instagram.com/wellnessgroveusa"),
    ("tiktok", "CRIS", "https://www.tiktok.com/@lee.sinmed"),
    ("tiktok", "CRIS 1", "https://www.tiktok.com/@sinchanmed"),
    ("instagram", "CRIS 2", "https://www.instagram.com/lee.sinmed"),
    ("instagram", "CRIS 3", "https://www.instagram.com/sinchan.med"),
    ("instagram", "EDUARDOD", "https://www.instagram.com/mike.navarro_"),
    ("instagram", "EDUARDOD 1", "https://www.instagram.com/earlinejones_"),
]


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    for i, (platform, name, url) in enumerate(PROFILES, 1):
        # ID format: {platform_prefix}-{index}-{name_prefix}
        name_prefix = name[:3].lower().replace(" ", "")
        profile_id = f"{platform[:2]}-{i}-{name_prefix}"
        conn.execute(
            "INSERT INTO profiles (id, platform, name, url) VALUES (?, ?, ?, ?)",
            (profile_id, platform, name, url),
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

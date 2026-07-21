from storage import get_conn

conn = get_conn()

# Delete rows where post_id is a full URL (the buggy entries)
# Keep the rows where post_id is a shortcode
deleted = conn.execute(
    "DELETE FROM posts WHERE post_id LIKE 'http%'"
)
print(f"Deleted {deleted.rowcount} duplicate posts (URL-based post_id)")

conn.commit()

# Verify remaining posts
rows = conn.execute(
    "SELECT post_id, published_at FROM posts "
    "WHERE profile_id = 'in-1-rob' ORDER BY published_at DESC LIMIT 15"
).fetchall()
print("\n=== robert.callahann (after cleanup) ===")
for r in rows:
    print(f"  {r['post_id']}  {r['published_at']}")

rows2 = conn.execute(
    "SELECT post_id, published_at FROM posts "
    "WHERE profile_id = 'in-2-dav' ORDER BY published_at DESC LIMIT 15"
).fetchall()
print("\n=== davidsdogtips (after cleanup) ===")
for r in rows2:
    print(f"  {r['post_id']}  {r['published_at']}")

conn.close()
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pydeps"))
import pymysql


connection = pymysql.connect(
    host="10.7.0.166",
    port=3306,
    user="root",
    password=os.environ["EDX_DB_PASSWORD"],
    database="openedx",
    charset="utf8mb4",
    cursorclass=pymysql.cursors.DictCursor,
    connect_timeout=10,
    read_timeout=30,
)

queries = {
    "relevant_columns": """
        SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, COLUMN_KEY
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = 'openedx'
          AND TABLE_NAME IN (
            'course_overviews_courseoverview',
            'student_courseenrollment',
            'certificates_generatedcertificate'
          )
        ORDER BY TABLE_NAME, ORDINAL_POSITION
    """,
    "certificate_statuses": """
        SELECT status, COUNT(*) AS rows_count,
               COUNT(DISTINCT CONCAT(course_id, '#', user_id)) AS unique_course_users
        FROM certificates_generatedcertificate
        GROUP BY status
        ORDER BY rows_count DESC
    """,
    "course_window_counts": """
        SELECT
          SUM(start >= '2026-01-01' AND start < '2027-01-01') AS starts_in_2026,
          SUM(start >= '2026-01-01' AND end < '2026-10-15') AS starts_2026_ends_by_oct14,
          SUM(start >= '2026-01-01' AND start < '2026-10-15') AS starts_by_oct14,
          SUM(start >= '2026-01-01' AND start < '2026-10-15' AND end IS NULL) AS starts_by_oct14_null_end
        FROM course_overviews_courseoverview
    """,
    "courses_in_requested_window": """
        SELECT id, display_name, start, end, directions, faculty
        FROM course_overviews_courseoverview
        WHERE start >= '2026-01-01'
          AND end < '2026-10-15'
        ORDER BY start, display_name
        LIMIT 200
    """,
}

with connection:
    with connection.cursor() as cursor:
        output = {}
        for name, query in queries.items():
            cursor.execute(query)
            output[name] = cursor.fetchall()
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))

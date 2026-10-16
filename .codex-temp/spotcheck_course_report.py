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
)

selected = """
    SELECT id, TRIM(display_name) AS course_name, start
    FROM openedx.course_overviews_courseoverview
    WHERE start >= '2026-01-01'
      AND start < '2026-10-15'
"""

with connection:
    with connection.cursor() as cursor:
        cursor.execute(f"""
            SELECT
                COUNT(*) AS course_runs,
                COUNT(DISTINCT course_name) AS distinct_course_names,
                MIN(start) AS minimum_start,
                MAX(start) AS maximum_start,
                (SELECT COUNT(DISTINCT CONCAT(e.course_id, '#', e.user_id))
                 FROM openedx.student_courseenrollment e
                 JOIN ({selected}) s1 ON s1.id = e.course_id
                ) AS all_enrollments,
                (SELECT COUNT(DISTINCT CONCAT(g.course_id, '#', g.user_id))
                 FROM openedx.certificates_generatedcertificate g
                 JOIN ({selected}) s2 ON s2.id = g.course_id
                 WHERE g.status = 'downloadable') AS downloadable_certificates
            FROM ({selected}) s
        """)
        controls = cursor.fetchone()
        cursor.execute(f"""
            SELECT course_name, COUNT(*) AS course_runs
            FROM ({selected}) s
            GROUP BY course_name
            HAVING COUNT(*) > 1
            ORDER BY course_name
        """)
        duplicates = cursor.fetchall()
        cursor.execute(f"""
            SELECT s.id AS course_id, s.course_name
            FROM ({selected}) s
            LEFT JOIN openedx.student_courseenrollment e ON e.course_id = s.id
            GROUP BY s.id, s.course_name
            HAVING COUNT(e.user_id) = 0
            ORDER BY s.course_name, s.id
        """)
        zero_enrollment_runs = cursor.fetchall()

print(json.dumps({
    "controls": controls,
    "duplicate_titles": duplicates,
    "zero_enrollment_runs": zero_enrollment_runs,
}, ensure_ascii=False, default=str))

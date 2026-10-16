import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pydeps"))
import pymysql


COURSES_SQL = """
WITH enrollments AS (
    SELECT course_id, COUNT(DISTINCT user_id) AS enrolled_students
    FROM openedx.student_courseenrollment
    GROUP BY course_id
),
certificates AS (
    SELECT course_id, COUNT(DISTINCT user_id) AS certified_students
    FROM openedx.certificates_generatedcertificate
    WHERE status = 'downloadable'
    GROUP BY course_id
),
course_runs AS (
    SELECT
        c.id AS course_id,
        COALESCE(NULLIF(TRIM(c.display_name), ''), 'Без названия') AS course_name,
        COALESCE(NULLIF(TRIM(c.directions), ''), 'Не указано') AS direction_name,
        COALESCE(NULLIF(TRIM(c.faculty), ''), 'Не указано') AS faculty_name,
        COALESCE(e.enrolled_students, 0) AS enrolled_students,
        COALESCE(cert.certified_students, 0) AS certified_students
    FROM openedx.course_overviews_courseoverview AS c
    LEFT JOIN enrollments AS e ON e.course_id = c.id
    LEFT JOIN certificates AS cert ON cert.course_id = c.id
    WHERE c.start >= '2026-01-01'
      AND c.start < '2026-10-15'
)
SELECT
    course_name,
    SUM(enrolled_students) AS enrolled_students,
    SUM(certified_students) AS certified_students,
    GROUP_CONCAT(DISTINCT direction_name ORDER BY direction_name SEPARATOR '; ') AS directions,
    GROUP_CONCAT(DISTINCT faculty_name ORDER BY faculty_name SEPARATOR '; ') AS faculties
FROM course_runs
GROUP BY course_name
ORDER BY course_name
"""

DIRECTIONS_SQL = """
WITH enrollments AS (
    SELECT course_id, COUNT(DISTINCT user_id) AS enrolled_students
    FROM openedx.student_courseenrollment
    GROUP BY course_id
),
course_runs AS (
    SELECT
        c.id AS course_id,
        COALESCE(NULLIF(TRIM(c.directions), ''), 'Не указано') AS direction_name,
        COALESCE(e.enrolled_students, 0) AS enrolled_students
    FROM openedx.course_overviews_courseoverview AS c
    LEFT JOIN enrollments AS e ON e.course_id = c.id
    WHERE c.start >= '2026-01-01'
      AND c.start < '2026-10-15'
)
SELECT direction_name, SUM(enrolled_students) AS enrolled_students
FROM course_runs
GROUP BY direction_name
ORDER BY enrolled_students DESC, direction_name
"""

connection = pymysql.connect(
    host="10.7.0.166",
    port=3306,
    user="root",
    password=os.environ["EDX_DB_PASSWORD"],
    database="openedx",
    charset="utf8mb4",
    cursorclass=pymysql.cursors.DictCursor,
    connect_timeout=10,
    read_timeout=60,
)

with connection:
    with connection.cursor() as cursor:
        cursor.execute(COURSES_SQL)
        courses = cursor.fetchall()
        cursor.execute(DIRECTIONS_SQL)
        directions = cursor.fetchall()
        cursor.execute("""
            SELECT COUNT(*) AS course_runs
            FROM openedx.course_overviews_courseoverview
            WHERE start >= '2026-01-01'
              AND start < '2026-10-15'
        """)
        course_runs = int(cursor.fetchone()["course_runs"])
        cursor.execute("""
            SELECT COUNT(*) AS duplicate_title_groups
            FROM (
                SELECT COALESCE(NULLIF(TRIM(display_name), ''), 'Без названия') AS course_name
                FROM openedx.course_overviews_courseoverview
                WHERE start >= '2026-01-01'
                  AND start < '2026-10-15'
                GROUP BY course_name
                HAVING COUNT(*) > 1
            ) AS duplicate_titles
        """)
        duplicate_title_groups = int(cursor.fetchone()["duplicate_title_groups"])

output = {
    "scope": "courses_started_2026-01-01_through_2026-10-14",
    "course_runs": course_runs,
    "duplicate_title_groups": duplicate_title_groups,
    "courses": courses,
    "directions": directions,
}

output_path = os.path.join(os.path.dirname(__file__), "course_report_data.json")
with open(output_path, "w", encoding="utf-8") as stream:
    json.dump(output, stream, ensure_ascii=False, indent=2, default=str)

print(json.dumps({
    "output_path": output_path,
    "course_rows": len(courses),
    "direction_rows": len(directions),
    "course_runs": course_runs,
    "duplicate_title_groups": duplicate_title_groups,
    "enrolled_total": sum(int(row["enrolled_students"]) for row in courses),
    "certified_total": sum(int(row["certified_students"]) for row in courses),
}, ensure_ascii=False))

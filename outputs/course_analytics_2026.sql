-- Отчет включает курсы с датой начала от 01.01.2026
-- до конца 14.10.2026 включительно.
-- Регистрации: все записи student_courseenrollment; is_active игнорируется.
-- Сертификаты: только certificates_generatedcertificate.status = 'downloadable'.
-- Курсы без регистраций сохраняются благодаря LEFT JOIN и отображаются со значением 0.

-- Лист 1. Сводка по курсам (одинаковые названия объединяются).
WITH enrollments AS (
    SELECT
        course_id,
        COUNT(DISTINCT user_id) AS enrolled_students
    FROM openedx.student_courseenrollment
    GROUP BY course_id
),
certificates AS (
    SELECT
        course_id,
        COUNT(DISTINCT user_id) AS certified_students
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
    LEFT JOIN enrollments AS e
        ON e.course_id = c.id
    LEFT JOIN certificates AS cert
        ON cert.course_id = c.id
    WHERE c.start >= '2026-01-01'
      AND c.start < '2026-10-15'
)
SELECT
    course_name AS `Название курса`,
    SUM(enrolled_students) AS `Зарегистрировано студентов`,
    SUM(certified_students) AS `Получили сертификат`,
    GROUP_CONCAT(DISTINCT direction_name ORDER BY direction_name SEPARATOR '; ') AS `Направление`,
    GROUP_CONCAT(DISTINCT faculty_name ORDER BY faculty_name SEPARATOR '; ') AS `Факультет`
FROM course_runs
GROUP BY course_name
ORDER BY course_name;

-- Лист 2. Аналитика по направлениям.
WITH enrollments AS (
    SELECT
        course_id,
        COUNT(DISTINCT user_id) AS enrolled_students
    FROM openedx.student_courseenrollment
    GROUP BY course_id
),
course_runs AS (
    SELECT
        c.id AS course_id,
        COALESCE(NULLIF(TRIM(c.directions), ''), 'Не указано') AS direction_name,
        COALESCE(e.enrolled_students, 0) AS enrolled_students
    FROM openedx.course_overviews_courseoverview AS c
    LEFT JOIN enrollments AS e
        ON e.course_id = c.id
    WHERE c.start >= '2026-01-01'
      AND c.start < '2026-10-15'
)
SELECT
    direction_name AS `Направление`,
    SUM(enrolled_students) AS `Сумма слушателей`
FROM course_runs
GROUP BY direction_name
ORDER BY `Сумма слушателей` DESC, direction_name;

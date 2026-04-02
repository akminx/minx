CREATE VIEW IF NOT EXISTS v_finance_monthly_spend AS
SELECT
    substr(posted_at, 1, 7) AS month,
    COALESCE(c.name, 'Uncategorized') AS category_name,
    SUM(t.amount) AS total_amount
FROM finance_transactions t
LEFT JOIN finance_categories c ON c.id = t.category_id
GROUP BY substr(posted_at, 1, 7), COALESCE(c.name, 'Uncategorized');

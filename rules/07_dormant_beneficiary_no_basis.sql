-- id: dormant_beneficiary_no_basis
-- level: hard
-- description: beneficiary approved more than 30 days ago with no bank payment older than 24 hours (dormant, or woken up today), paid without an approved unused basis of this client for this beneficiary
-- params: :api_key_id :account_id :beneficiary_id :amount_aed :now :basis_id
WITH ben AS (
    SELECT (SELECT approved_at FROM bank.beneficiaries WHERE id = :beneficiary_id) AS approved_at
),
old AS (
    SELECT COUNT(*) AS n
    FROM bank.payments
    WHERE beneficiary_id = :beneficiary_id
      AND created_at < :now
      AND julianday(:now) - julianday(created_at) > 1
),
basis AS (
    SELECT COUNT(*) AS n
    FROM bank.bases b
    JOIN bank.api_keys k ON k.client_id = b.client_id
    WHERE k.id = :api_key_id
      AND b.id = :basis_id
      AND b.beneficiary_id = :beneficiary_id
      AND b.approved = 1
      AND b.used = 0
)
SELECT COALESCE(julianday(:now) - julianday(ben.approved_at) > 30, 0)
       AND old.n = 0
       AND basis.n = 0 AS violated,
       'approved ' || COALESCE(printf('%.0f', julianday(:now) - julianday(ben.approved_at)), '?')
       || ' days ago, payments older than 24 h: ' || old.n
       || ', valid basis: ' || CASE WHEN basis.n > 0 THEN 'yes' ELSE 'no' END AS detail
FROM ben, old, basis;

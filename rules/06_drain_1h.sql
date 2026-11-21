-- id: drain_1h
-- level: hard
-- description: more than 30% of the account balance reserved in 1 hour
-- params: :api_key_id :account_id :beneficiary_id :amount_aed :now :basis_id
-- Balance at the start of the window = current balance + payments executed in the window.
WITH win AS (
    SELECT COALESCE(SUM(amount_aed), 0) AS reserved,
           COALESCE(SUM(CASE WHEN status = 'executed' THEN amount_aed END), 0) AS executed
    FROM reservations
    WHERE account_id = :account_id
      AND status IN ('pending','executed','held','released')
      AND created_at >= datetime(:now, '-1 hour')
      AND created_at <= :now
),
acc AS (
    SELECT COALESCE((SELECT balance_aed FROM bank.accounts WHERE id = :account_id), 0) AS balance
)
SELECT win.reserved > 0.3 * (acc.balance + win.executed) AS violated,
       'reserved in 1 h: ' || printf('%.0f', win.reserved)
       || ' of balance ' || printf('%.0f', acc.balance + win.executed) AS detail
FROM win, acc;

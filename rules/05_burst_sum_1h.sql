-- id: burst_sum_1h
-- level: hard
-- description: sum reserved by one API key in 1 hour is above AED 200,000
-- params: :api_key_id :account_id :beneficiary_id :amount_aed :now :basis_id
SELECT COALESCE(SUM(amount_aed), 0) > 200000 AS violated,
       'reserved in 1 h: ' || printf('%.0f', COALESCE(SUM(amount_aed), 0)) AS detail
FROM reservations
WHERE api_key_id = :api_key_id
  AND status IN ('pending','executed','held','released')
  AND created_at >= datetime(:now, '-1 hour')
  AND created_at <= :now;

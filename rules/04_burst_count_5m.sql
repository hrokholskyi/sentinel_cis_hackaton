-- id: burst_count_5m
-- level: hard
-- description: more payments from one API key in 5 minutes than its limit
-- params: :api_key_id :account_id :beneficiary_id :amount_aed :now :basis_id
SELECT COUNT(*) > 8 AS violated,
       'payments in 5 min: ' || COUNT(*) AS detail
FROM reservations
WHERE api_key_id = :api_key_id
  AND status IN ('pending','executed','held','released')
  AND created_at >= datetime(:now, '-5 minutes')
  AND created_at <= :now;

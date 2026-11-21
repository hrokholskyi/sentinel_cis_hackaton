-- id: amount_above_typical
-- level: soft
-- description: beneficiary has prior payments and the amount is more than 1.5 x the largest of them
-- params: :api_key_id :account_id :beneficiary_id :amount_aed :now :basis_id
SELECT COUNT(*) > 0 AND :amount_aed > 1.5 * COALESCE(MAX(amount_aed), 0) AS violated,
       'prior payments: ' || COUNT(*) || ', prior max: ' || printf('%.0f', COALESCE(MAX(amount_aed), 0))
       || ', amount: ' || printf('%.0f', :amount_aed) AS detail
FROM bank.payments
WHERE beneficiary_id = :beneficiary_id
  AND created_at < :now;

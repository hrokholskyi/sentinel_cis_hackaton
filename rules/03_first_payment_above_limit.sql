-- id: first_payment_above_limit
-- level: soft
-- description: beneficiary has zero prior payments and the amount is at or above the soft minimum
-- params: :api_key_id :account_id :beneficiary_id :amount_aed :now :basis_id :soft_min_amount_aed
SELECT COUNT(*) = 0 AND :amount_aed >= :soft_min_amount_aed AS violated,
       'prior payments: ' || COUNT(*) || ', amount: ' || printf('%.0f', :amount_aed) AS detail
FROM bank.payments
WHERE beneficiary_id = :beneficiary_id
  AND created_at < :now;

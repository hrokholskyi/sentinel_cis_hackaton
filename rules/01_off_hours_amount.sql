-- id: off_hours_amount
-- level: soft
-- description: payment outside 07:00-20:00 local time with an amount at or above the soft minimum
-- params: :api_key_id :account_id :beneficiary_id :amount_aed :now :basis_id :soft_min_amount_aed
SELECT (time(:now) < '07:00:00' OR time(:now) > '20:00:00')
       AND :amount_aed >= :soft_min_amount_aed AS violated,
       'local time ' || time(:now) || ', amount ' || printf('%.0f', :amount_aed) AS detail;

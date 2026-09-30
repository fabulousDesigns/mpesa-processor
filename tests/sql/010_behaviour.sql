INSERT INTO users VALUES ('u1','+254110026199','BERNARD','MAINA');
INSERT INTO stmt_requests (id,user_id,msisdn,status,consent_at,consent_version,expires_at)
VALUES ('r1','u1','254110026199','AWAITING_CODE',NOW(3),'v1',NOW(3)+INTERVAL 30 MINUTE);

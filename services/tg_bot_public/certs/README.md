# T-Bank TLS certificates

These certificates are loaded only by `TBankClient`. They are not added to the
global bot or container trust store.

Source: the certificate downloads linked by T-Bank's official installation
guide: <https://developer.tbank.ru/docs/tls-settings>.

| File | Subject | SHA-256 fingerprint | Valid until |
| --- | --- | --- | --- |
| `russian_trusted_root_ca.pem` | Russian Trusted Root CA | `D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31` | 2032-02-27 |
| `russian_trusted_sub_ca.pem` | Russian Trusted Sub CA | `BB:BD:E2:10:3E:79:0B:99:9E:C6:2B:D0:3C:F6:25:A5:A2:E7:C3:16:E1:0A:FE:6A:49:0E:ED:EA:D8:B3:FD:9B` | 2027-03-06 |

When rotating either file, verify its subject, issuer, expiry and fingerprint
before committing it. The intermediate must verify against the root.

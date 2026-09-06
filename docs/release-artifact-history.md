# Release artifact history

再生成可能な旧binaryを削除してもrelease判断を追跡できるよう、source commitと既知のchecksumを保持する。

| Version | Source commit | Artifact | Size (bytes) | SHA-256 |
|---|---|---|---:|---|
| v0.1 | `23153968bcc7c6193d7fe8d99f40f0b17f3efaa1` | release artifacts | tracked release記録参照 | `docs/unit5-release.md`参照 |
| v0.1.1 | `fcfe281153a3c891a1ca03df086e765491694d8c` | `DJDmaker.exe` | 3,757,199 | `08C00808945B7E5759E0605676E5BADD5FE38836A1EB97ACE16071E7ED19F945` |
| v0.1.2 | `0e85dbc9926e671f98258af501ec0d11ea547d81` | `DJDmaker.exe` | 3,764,822 | `2FAB1800108806173780C107DC0B9814FDC7837879BFE5A39D33A4226D80C158` |
| v0.1.2 | `0e85dbc9926e671f98258af501ec0d11ea547d81` | `DJDmaker_v0.1.2.zip` | 271,847,117 | `10E0751E43A2C1A67F43671931AFAA45D8D42EA51AFD417E6E1E242C54AB9081` |
| v0.1.3 auth release | `e9982c4f1d4dfc8ff3e075d969ff9fe578926ba3` | `DJDmaker.exe` | 3,768,060 | `AE1CCB69B3F5E232D0F25EE7C7315B3E68954BBAA641B7F7A8AC77AF1752C281` |
| v0.1.3 auth release | `e9982c4f1d4dfc8ff3e075d969ff9fe578926ba3` | `DJDmaker_v0.1.3.zip` | 277,558,227 | `D56CA4CD7393709DA7665B1CABABFFAD91A292AB0C2299F5C993B3931303628D` |
| v0.1.3 preset restore candidate | this change | `DJDmaker.exe` | 3,781,271 | `DA8367D4B959C829ACF93677EBC1D1C78C6DEB1F6E42EDAF6BA6B911B2CE1761` |
| v0.1.3 preset restore candidate | this change | `DJDmaker_v0.1.3.zip` | 277,568,884 | `1F69185DE418732BFD5162DC89D439074D01A50C9968D5445320D3D68621B4AF` |

PyInstallerと依存packageはbyte-for-byte再現を保証していないため、source commit、build script、spec、FFmpeg license/inputを合わせて保全する。

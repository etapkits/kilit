# Etakit Kilit

Pardus ETAP etkileşimli tahtalar için ekran kilidi. Tahta açılışta masaüstünü kilitler ve ekranda kısa ömürlü bir karekod gösterir. Yetkili kişi [Etakit](https://etakit.com.tr) sitesinde bu kodu telefonuyla okutunca tahta açılır. Kilitleme ve kapatma siteden de gönderilebilir.

Tahta sunucuya kendisi bağlanır; tahtaya dışarıdan kapı açılmaz. Aynı okul ağında ayrıca bir bilgisayar gerekmez.

## Nasıl çalışır

1. Kullanıcı ETAP oturumuna girince kilit tam ekran açılır, klavye ve dokunmatiği tutar. Giriş ekranında ve çıkışta çalışmaz.
2. Tahta ilk bağlantıda kurumun kayıt anahtarıyla sunucuya kaydolur ve bir cihaz jetonu alır. Sitede yönetici onaylayana kadar "onay bekliyor" görünür.
3. Kilit ekranında tahta kodu ve yaklaşık 20 saniyede bir yenilenen karekod vardır. Tahta her yeni kodu sunucuya bildirir; ekranın fotoğrafı kısa süre sonra geçersiz olur.
4. Sitede okutulan kod, tahtanın son bildirdiği kodla eşleşirse sunucu açma izni yazar. Tahta bu izni açık tuttuğu bağlantıdan alır ve masaüstünü açar.
5. Tahta durumunu (kilitli, açık, kapanıyor) düzenli bildirir ve komut bekler.

## Kilitlenme durumları

- **Boşta kalma:** Belirlenen süre dokunulmazsa kilitlenir.
- **Kilitle düğmesi:** Açıkken sağ alt köşede durur.
- **Siteden kilitleme:** Kısa bir geri sayımdan sonra kilit ekranına döner.
- **Siteden kapatma:** Tahta kapanır.
- **Sunucu kopması:** Açıkken sunucuya belirlenen süre ulaşamazsa kilitlenir. Sunucuya ulaşamayan tahta kilitli kalır ve açılamaz.
- **Oturum süresi:** Ayarlanmışsa, son açılıştan bu süre sonra oturum kapanır.

## Acil açılış

Paket bir acil PIN ile üretilmişse (veya sitedeki kilit ayarlarında PIN tanımlıysa), kilit ekranından PIN girilerek tahta sınırlı bir süre açılabilir. Süre dolunca kilit geri gelir. Art arda yanlış denemede PIN bir süre kilitlenir. PIN dosyada düz metin değil, PBKDF2 özeti olarak durur.

## Sunucu ayarları

Sitede kurum yöneticisinin "Kilit ayarları" sayfasındaki değerler tahtaya iner ve yerel ayarın üzerine yazılır: boşta kilitlenme süresi, kilit geri sayımı, bağlantı kopunca bekleme, acil açılış süresi, oturum süresi ve acil PIN. Sitede kayıt yoksa paketteki değerler geçerli olur. Tahta bu ayarları sunucuya yazmaz.

## Paket üretme

Paket Pardus veya Debian tabanlı bir sistemde üretilir:

```sh
cd pardus
./build-deb.sh --server-url https://etakit.com.tr --enrollment-key KURUM_ANAHTARI --emergency-pin 123456
```

- `--server-url`: Tahtanın bağlanacağı site adresi, sonda `/` olmadan. Verilmezse tahta ilk açılışta ayar penceresinde sorar.
- `--enrollment-key`: Kurumun tahta kayıt anahtarı. Süper yönetici panelindeki kurum listesinde görünür.
- `--emergency-pin`: 4-8 haneli acil PIN (isteğe bağlı).

Çıktı `pardus/dist/etakit-kilit_<sürüm>_all.deb` olarak yazılır. Sürüm `pardus/packaging/control` dosyasındadır.

## Kurulum

Tahtada yönetici yetkisiyle:

```sh
sudo apt install ./etakit-kilit_1.4.24_all.deb
```

Bağımlılıklar: `python3 (>= 3.9)`, `python3-gi`, `gir1.2-gtk-3.0`, `python3-cairo`, `python3-qrcode`, `libxss1`, `polkitd` veya `policykit-1`, `sudo`.

Sunucu adresi kayıtlı değilse kurulumdan hemen sonra ayar penceresi açılır. Ayarlar sonradan uygulama menüsündeki **Etakit Kilit Ayarları** ile değiştirilebilir. Paket güncellenince tahta kimliği ve jetonu korunur; tahta sitede aynı kayıtla kalır.

## Ayar dosyası

Ayarlar `/etc/etakit/kilit.conf` dosyasındadır. Tahta kimliği ve cihaz jetonu `/var/lib/etakit` altında tutulur.

| Anahtar | Açıklama | Varsayılan |
| --- | --- | --- |
| `server_url` | Site adresi (`http://` veya `https://`) | boş |
| `enrollment_key` | Kurumun tahta kayıt anahtarı | boş |
| `idle_seconds` | Boşta kilitlenme süresi (sn) | `600` |
| `lock_countdown_seconds` | Siteden kilitlemede geri sayım (sn) | `10` |
| `offline_grace_seconds` | Sunucu kopunca kilitlenmeden önce bekleme (sn) | `45` |
| `heartbeat_seconds` | Durum bildirme aralığı (sn) | `10` |
| `command_wait_seconds` | Komut bağlantısının açık tutulma süresi (sn) | `20` |
| `tls_verify` | HTTPS sertifikasını doğrula (`yes`/`no`) | `yes` |
| `state_dir` | Tahta kimliği ve jetonun tutulduğu klasör | `/var/lib/etakit` |
| `emergency_pin_hash` | Acil PIN özeti (paket üretilirken yazılır) | boş |
| `emergency_seconds` | Acil açılış süresi (sn) | `300` |
| `emergency_max_attempts` | PIN kilitlenmeden önceki deneme sayısı | `5` |
| `emergency_lockout_seconds` | Yanlış denemelerden sonra PIN bekleme süresi (sn) | `300` |
| `session_seconds` | Oturum süresi, `0` sınırsız (sn) | `0` |

## Geliştirme

```sh
cd pardus
python3 -m unittest discover -s tests
```

Klasör yapısı:

- `pardus/etakit_kilit/`: Kilit uygulaması (Python, GTK 3)
- `pardus/bin/etakit-kilit`: Başlatıcı
- `pardus/packaging/`: Debian paket betikleri, systemd kullanıcı servisi, polkit ve LightDM ayarları
- `pardus/tests/`: Birim testleri
- `pardus/build-deb.sh`: Paket üretme betiği

## Sunucu tarafı

Tahtanın konuştuğu API Etakit web sunucusundadır:

| Yöntem | Adres | Amaç |
| --- | --- | --- |
| `POST` | `/api/device/register` | Kayıt anahtarıyla kaydolur, cihaz jetonu alır |
| `POST` | `/api/device/heartbeat` | Durum bildirir |
| `POST` | `/api/device/name` | Tahta adını bildirir |
| `POST` | `/api/device/qr` | Ekrandaki karekodu bildirir |
| `GET` | `/api/device/settings` | Kurumun kilit ayarlarını alır |
| `GET` | `/api/device/commands?wait=20` | Komut bekler |
| `POST` | `/api/device/commands/{id}/ack` | Komutu onaylar |

Kayıttan sonraki istekler `Authorization: Bearer <cihaz jetonu>` taşır. Karekod biçimi `etakit:1:<tahta kodu>:<tek kullanımlık değer>` şeklindedir.

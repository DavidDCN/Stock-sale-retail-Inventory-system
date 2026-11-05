# How the phones talk to the laptop (a beginner's guide)

This is what we learned getting the sales persons' phones to send receipt photos to the shop laptop: what worked,
what failed, why, and the words you need to know to fix it yourself next time.

---

## 1. The picture in one sentence

The laptop runs a small **web server** (the shop program, on **port 8000**). The phones are **clients**: they send
photos to `http://<laptop's address>:8000`. For that, phone and laptop must be on the **same local network** and
nothing in between may block the phone's request.

```
 Phone (client)  ──Wi-Fi──►  Router / access point  ──►  Laptop (server, port 8000)
   192.168.0.119               192.168.0.1                  192.168.0.121
```

---

## 2. Words to remember

| Term | What it means here | How to see it |
|---|---|---|
| **IP address** | The "house number" of a device on the network, e.g. `192.168.0.121`. | Laptop: `ipconfig` in cmd. Phone: Wi-Fi → the network → details. |
| **LAN** (local network) | All devices connected to the same router / Wi-Fi. | — |
| **Subnet** | Devices whose addresses start the same (`192.168.0.___` with mask `255.255.255.0`) can talk directly. | Compare the first three numbers. |
| **Router** | The box that makes the Wi-Fi network and gives out addresses. | Usually `192.168.0.1` or `192.168.1.1` (the "gateway"). |
| **Gateway** | The router's address; where traffic to the internet goes. | `ipconfig` → Default Gateway. |
| **DHCP** | The router's job of handing each new device an IP address automatically. | "Obtaining IP address…" on a phone = DHCP is not answering. |
| **169.254.x.x** | The address Windows gives itself when **no DHCP answered**. Seeing it = broken sharing. | `ipconfig` |
| **Port** | A "door number" on a device. Our program listens on **8000** (photos) and **8765** (the phone finder). | — |
| **Server / client** | The laptop serves; the phones ask. | — |
| **Firewall** | Windows' guard that blocks or allows incoming connections. It has 3 **profiles**: Domain, **Private**, **Public**. | Windows Security → Firewall & network protection. |
| **Network profile** | Windows labels each Wi-Fi *Private* (home/shop: trusted) or *Public* (café: blocks almost everything). A new Wi-Fi starts as Public. | Settings → Network → the Wi-Fi → Network profile type. |
| **UDP broadcast** | A "shout to everyone on the Wi-Fi". The phone shouts *"where is the shop PC?"* on port 8765; the laptop answers. | — |
| **Extender / repeater** | A device that repeats another router's Wi-Fi further away (our **TP-Link**). | — |
| **Client isolation** (AP isolation) | An access-point setting that stops Wi-Fi devices from talking **to each other** (only to the internet). Common on extenders, guest networks and public Wi-Fi. | In the extender's settings page (needs its admin password). |
| **VPN** | Software that sends traffic through a tunnel (e.g. **Tailscale**). It can hijack replies meant for the local network. | A network adapter named after the VPN. |
| **Mobile Hotspot** | Windows turns the laptop into a small Wi-Fi router of its own. | Settings → Network → Mobile hotspot. |
| **ICS** (Internet Connection Sharing) | The Windows service that makes the hotspot work: it hands out addresses (**192.168.137.x**) and shares the laptop's internet. | `ncpa.cpl` → Wi-Fi → Properties → **Sharing**. |
| **NAT** | What ICS does: many phones share the laptop's one internet connection. | — |
| **Wi-Fi Direct Virtual Adapter** | The "network card" Windows creates for the hotspot. It shows as **Local Area Connection\* 2** (the number can differ). | `ncpa.cpl` |

---

## 3. What worked first: same Wi-Fi router

At first the laptop and phones were on a normal router. The program:

1. listens on **all** the laptop's addresses (`0.0.0.0:8000`), not only on itself;
2. answers the phones' **UDP shout** on port 8765, so the app finds the laptop by itself
   (if the shout is blocked, the app knocks on every address `x.x.x.1 – 254` on port 8000);
3. asks Windows (one **Yes**) to set the Wi-Fi to **Private** and to **allow** the program through the firewall.

**Rule:** phone and laptop on the same Wi-Fi + same subnet + firewall allowing port 8000 = it connects.

---

## 4. What failed: the TP-Link extender

At the shop the only Wi-Fi is a **TP-Link extender**. We checked everything step by step:

| Check | Result |
|---|---|
| Same Wi-Fi? | Yes, both on the extender. |
| Same subnet? | Yes, laptop `192.168.0.121`, phone `192.168.0.119`, mask `255.255.255.0`. |
| Windows firewall | Off for Private: nothing blocked on the laptop. |
| VPN (Tailscale) | Stopped. No odd routes left (`route print`). |
| Phone's mobile data | Turned off: no difference. |
| Did any phone request reach the laptop? | **No.** The program's log showed zero requests from the phone. |

When everything on both ends is open and the request still never arrives, the device **in the middle** is
dropping it: the extender's **client isolation**. It lets each device reach the internet but not each other.

**Fix options** (in order of preference):

1. **Turn off client / AP isolation** in the extender's settings, if you have its admin password (TP-Link:
   `tplinkrepeater.net`). Then everything works with no other change.
2. **Use a real router** (or the main router's Wi-Fi) instead of the extender for the shop network.
3. **The laptop's own Wi-Fi** (what we did, next section).

---

## 5. What we did: the laptop's own Wi-Fi (Mobile Hotspot + ICS)

The program turns on **Windows Mobile Hotspot** with its own name (*TresNinos-Shop*) and password, so the phones
connect **directly to the laptop**; the extender is no longer in the middle.

```
                       TP-Link extender (internet)
                                 ▲
                                 │ Wi-Fi (laptop = a normal client)
 Phones ──TresNinos-Shop Wi-Fi──►  LAPTOP  (192.168.137.1, shop program on port 8000)
 192.168.137.x                    ICS shares the internet to the phones (NAT)
```

### The one manual step: link the sharing (`ncpa.cpl`)

The phones first stayed on **"Obtaining IP address…"** and the hotspot adapter showed a **169.254** address.
That means ICS was not handing out addresses: its sharing was linked to the wrong adapter (*Ethernet*, an
unplugged cable port, and an old leftover connection). The fix:

1. Turn the program's **Phones' own Wi-Fi ON** (so the hotspot adapter exists).
2. Press **Windows + R**, type `ncpa.cpl`, press Enter.
3. Right-click **Wi-Fi** (the one with internet) → **Properties** → **Sharing**.
4. If it is ticked with the wrong connection: untick → OK, open it again.
5. Tick **"Allow other network users to connect through this computer's Internet connection"**.
6. In **Home networking connection** choose **Local Area Connection\* 2** (the Wi-Fi Direct Virtual Adapter).
7. OK (yes to the 192.168.137.1 message). Turn the hotspot off and on; on the phone *Forget* and reconnect.

> Note: this is **not** the Windows "Network Bridge" feature. A bridge joins two networks into one; ICS
> instead makes the laptop a small **router** (its own network 192.168.137.x, with NAT to the internet).
> Windows sometimes forgets this link after big updates: if phones get stuck on "Obtaining IP address" again,
> repeat the steps above.

---

## 6. Limits: laptop Wi-Fi vs a normal router

| | Laptop's own Wi-Fi (Mobile Hotspot) | Normal Wi-Fi router |
|---|---|---|
| Phones at once | **8 at most** (Windows limit) | Usually 30–100+ (a /24 network has 253 addresses) |
| Range | About one room, ~10–15 m (a laptop antenna) | A whole shop / house; more with a mesh |
| Laptop must be… | **On, awake, program open** (the program keeps it awake) | Router is always on; laptop only needed for the program |
| Speed | The laptop's one Wi-Fi card does two jobs (receive + hotspot), so it is slower | Full speed |
| Internet for phones | Through the laptop (if the laptop has internet) | Directly |
| Setup | One-time `ncpa.cpl` sharing link; may need redoing after Windows updates | None |
| Client isolation problem | Gone (phones talk to the laptop directly) | Depends on the device (extenders and guest networks often isolate) |

**Good long-term setup:** a small router of your own for the shop (isolation off, laptop on it by cable or
Wi-Fi). Then no hotspot is needed and any number of phones work.

---

## 7. Quick troubleshooting card

| Phone says / you see | Likely cause | Do this |
|---|---|---|
| "Could not find the shop PC" | Not on the same network, firewall, isolation, or VPN | Same Wi-Fi? Fix it button in the program; stop VPNs; try the laptop's own Wi-Fi. |
| "Obtaining IP address…" on the laptop's Wi-Fi | ICS not linked (169.254 address) | `ncpa.cpl` sharing steps (section 5). |
| Connected, but "no internet" | The laptop itself has no internet | The shop app still works; only internet is missing. |
| Worked yesterday, not today | Laptop got a new address, Windows set the Wi-Fi to Public, or the hotspot turned off | Program: profile box shows the address + Fix it; the hotspot turns itself on again within a minute. |
| Test from the phone's browser | — | `http://<laptop address>:8000/api/ping` shows `tres-ninos-shop` = the network is fine. |

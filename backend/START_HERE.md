# Backend — start here

Everything the shop saves is in **one file: `inventory.db`** (SQLite, on this laptop). No Supabase, no cloud.
With `python server.py` it is `backend/inventory.db`; with **TresNinosShop.exe** it is in
`C:\Users\<you>\AppData\Local\TresNinosShop` (so the program folder never contains records).
It is created EMPTY the first time the server starts.

## Every day
Double-click **TresNinosShop.exe** (once you have built it, see `BUILD_EXE.md`). Or, without the .exe:
```
cd C:\Users\David\Desktop\TN\program\Tress\backend
python server.py serve
```
then open http://127.0.0.1:8000 (after `npm run build`), or run `npm run dev` in the `Tress` folder while changing the code.

**Signing in:** the system password (top box) + your own username and password. The eye button shows what you typed.
The desktop pages only open on this laptop; phones and other PCs on the Wi-Fi are refused (security). To also allow
another PC: `python server.py serve --allow-remote-desk`.

## First time only (or the first start after this update)
1. `pip install -r requirements.txt` (PaddleOCR you already have).
2. Start the server, open the React app → **First run: set the system password**. Choose the shop's system password,
   then your own username (this is your name everywhere) and password. (Already have a desktop account, e.g. `alice`? Type its username and
   password there to keep it.)
3. Anyone you trust who knows the system password presses **New person** on the sign-in screen and makes their own
   username + password. The **Calendar** page shows, per day, who was in charge (first and last time), what was sold,
   the stock deducted, who was present and everything they did — and lets you download that day as a ZIP.
4. Supply → **Add Item**: a photo (optional), the types, and the **A / B / C alert levels** (stock at or below A =
   critical, top of the dashboard Priority; B = low; C = watch).
5. Cash Flow → **Employee**: photo (optional) and, for sales people, a **Phone login**. That username + password is all
   the phone needs.

## The phone (Flutter) app
**Round 17 changed the phone app** (Cash/Bank question, New item, delete a line, how many are left): build it again
from the `shop_phone_app` folder (Android Studio ▶ Run, or `flutter build apk`) and install it on each phone. An old
phone app still works with this server (its sales are counted as cash).
The phone only asks for the username + password. It finds this PC by itself on the same Wi-Fi:
first the address that worked last time, then it asks the Wi-Fi (UDP port 8765, this server answers), then it scans
the Wi-Fi for port 8000. If the laptop gets a new address from the router, the phone finds it again by itself.
If Windows asks whether Python may use the network, allow it on **Private networks** (that covers both ports).

## Handy commands
```
python server.py list-users
python server.py set-system-password                       # forgot the system password
python server.py add-user owner --name "Owner" --admin     # extra desktop account from the command line
python server.py passwd <username>
python server.py seed-demo                                 # 7 sample nail/screw items, only for testing the OCR
python server.py cleanup-images --days 365                 # delete old receipt photos (or: Calendar → Archive & free space)
```
Backup = copy `inventory.db` (with the server stopped) plus the `avatars` and `uploads` folders.

## Progression → Cash In vs Cash Out
The right side of Progression shows one ring: money in (sales per sales person, bulk order payments, cash added) and
money out (payroll, each loan / bill, each maintenance item such as "Truck maintenance", stock purchases, cash advances).
Click a slice to see every payment in it. Choose This month / 3 months / 12 months / All.

## Your profile picture
Click your name at the bottom of the left menu → Change photo. It shows on the sign-in screen, the top bar and the
Calendar.

## To save (bottom of Cash In vs Cash Out)
Costs that come every few months count as their monthly share (change oil ₱2,500 every 6 months = ₱417 a month).
"To save" shows what to put aside this month, how much should already be saved for each such cost, and the restock
fund: the original (cost) price of what was sold since you last bought it, so you can buy it again. If you raised
the cost price, it shows the extra that comes out of the profit.
**Only items at their C / B / A level are in the restock fund:** an item joins when its stock drops to C and leaves by
itself once you buy it again (Supply → the item → Edit → Buy stock) or correct the count.
**Auto inventory** (the button right above the restock fund): every item at its C / B / A level, most urgent first,
how many are left, what was sold since you last bought it, how many to buy (at least enough to be above C again) and
what that costs. An item that has no levels (pieces, rolls) shows only when it ran out.

## Supply: cost price = 65% when you did not give one
Your inventory list has only selling (cash) prices. When an item has no cost price, the system takes **65% of the
selling price** (sell ₱100 → cost ₱65): Supply shows "65% of the selling price — no cost given" under it, and it
follows the selling price when you change it. Type the real cost in Edit when you know it; empty or 0 = 65% again.
Profit, the restock fund and Auto inventory all use this cost.

## Payments (menu, below Calendar)
Every payment is **Cash** or **Bank**, nothing more. No bank account is added or linked (the system never connects to a
bank): the bank list simply adds up what was paid by bank (GCash, Maya, InstaPay count as bank too).
- **Cash or bank of a sale** is chosen by the sales person on the phone when pressing Update ("How did the customer
  pay? Cash / Bank"; since round 17 — before, it was read from the photo). Payments by day shows it on every sale with
  the reason ("chosen by <the sales person> on the phone"); open a sale and press "make it bank / make it cash" to change it.
- **Order payments** (Sales → Add Order, the order's payment log, or Payments → Incomplete & missing → Record a payment):
  pick **Cash** or **Bank** when you record it.
- **Cash payments / Bank payments**: short lists, one line per sales person per day or per order payment: date, name,
  amount and the total so far, month by month (or All months). Click a line to open that day in Payments by day.
- "Incomplete & missing" lists who still owes; click a customer to see each payment or record the next one.

## After every update: restart the server
The page and the server are updated together. If the server window still runs the OLD server, new buttons answer
"Method Not Allowed" and a red banner "Restart the shop server" appears at the top of the page. Click the window where
`python server.py serve` runs, press **Ctrl+C**, run `python server.py serve` again; the banner goes away by itself.

## Removing a person (Temporary → Done, Cash Flow → an employee → Remove…)
First a popup shows exactly what the shop still owes them: days × day rate, overtime, undertime, bonuses, cash
advances, what was already paid, and **still owed**. "Pay ₱… and remove" records that last pay (payroll "Final pay");
"Remove without paying now" keeps what is owed visible. They are hidden, not deleted: their days stay in the records,
their phone login is switched off and they are taken off the deliveries still to come. **Show hidden** (Temporary,
General, Payslip, Cash Flow → Employees, Sales counters) lists them again with **Bring back**. "Added by mistake?
Delete every record" is the only real delete. Every password box has an eye button to see what you typed.

## Testing the newest version WITHOUT building the .exe
`npm run build` only makes the pages (the `dist` folder); it is NOT build_exe.bat and makes no program for another
laptop. Close TresNinosShop first (both use port 8000). Two ways on this laptop:

**Fastest while testing (no build at all, changes show at once):** two terminals
```
cd C:\Users\David\Desktop\TN\program\Tress\backend
python server.py serve
```
```
cd C:\Users\David\Desktop\TN\program\Tress
npm run dev
```
then open the address `npm run dev` prints (http://localhost:5173). Every saved code change appears in the page by itself.

**Like the real program:** `npm run build` (a few seconds), then `python server.py serve` in backend and open
http://127.0.0.1:8000.

Both use the TEST database `backend\inventory.db`, not the one TresNinosShop.exe uses (`%LOCALAPPDATA%\TresNinosShop`),
so testing never touches the shop's real records. Build the .exe for laptop 2 only when everything is approved.

## Dashboard → Employee Attendance tabs
Employee Attendance (check in, as before) · Temporary (add extra help for busy days; "Done" takes them off the list) ·
Overtime (a date, Overtime or Undertime, then "Everyone present" + Apply to all, or "Only these people": search a
name, add, type their hours; Save) · Payslip (this week / last 15 days / this month or any dates: each person's total;
Edit = fix one day; Details = every day with present, overtime, undertime and the day's pay). Cash Flow's Overtime and
Undertime (hours) follow what is saved here. **General** = the whole month at once: pick the dates, Apply to all,
uncheck who was absent (click a day for everyone that day, a name for all their days), Save.

## Calendar → Open saved data
Read data you moved off this laptop: choose a TresNinos_archive ZIP, a day ZIP or an inventory_backup.db (from the
USB drive). Pick a day: who was present, each sales person with the items they sold, the orders and the deliveries of
that day (truck, driver, helpers, delivered). Only the database is copied here (small); photos stay in your file. The
trash button removes the copy.

## Transfer (menu, below Payments)
Laptop 1: Make setup file (employees with photos and records, loans & bills, maintenance, benefits, supply with prices
and levels, trucks, orders, cash / bank payment lines, cash entries). Copy it to laptop 2. Laptop 2: Read a setup file → see what
is new / changed / the same → tick → Apply. Nothing is deleted and applying twice adds nothing twice. Sales and
receipts move with Calendar → Archive; accounts and passwords never move.

**Include the sales** (switch on the Transfer page): the setup file also carries every sale (items + sales person +
cash / bank), so the other laptop can read and keep them. Pending delivery orders from the phones move too, with their
receipt photo. Truck runs (Planner: day, truck, driver, helpers, delivered) move with the orders, also for orders the
other laptop already has.

## Planner (menu, below Transfer)
The deliveries of the orders on a calendar. Each truck run has a **driver** (the person in charge) and **helpers**
(companions): pick them in Sales → Add Order (each truck run) or right in the Planner. Only the employees whose job is
Driver are in the driver list; the helper list has the labor and that day's temporary workers. Click a day: every
delivery (whom, where, which truck, the items), who goes, and who is still free. Not enough people? Add temporary help
right there. "Mark delivered" when the truck is back.

## Delivery receipts from the phones
When a sales person sends a photo of a DELIVERY receipt (it says "Delivery receipt", "Deliver to", "D.R. No."...), the
order is read for you: a green bubble appears on that person's counter in Sales and a green card at the top of the
dashboard. Open it: the Add Order form is already filled in (customer, address, items, day) with the photo, how it was
paid (cash / bank) and every row marked "please check" (handwriting). Check it, pick the truck, driver and helpers, and
press Apply. "Not a delivery order" dismisses it. **Read the photo again** lets the reader read it once more (for
example after you added its items to Supply). A tilted photo is fine: the rows are straightened before reading.

## Your real shop data: the starter file
`starter\TresNinos_START_real_inventory.zip` holds the shop's REAL inventory (from Hardware_Inventory.docx: 46 items,
138 types; the cash price = the selling price, the cost = 65% of it) and the 7 sales persons with their phone logins.
- Boysen: one item **Boysen** with every color as a type, liter and gallon separately ("Flat Latex White (Liter)",
  "Flat Latex White (Gallon)"...). Every other heading of the list is an item, its lines are the types.
- Alert levels (A / B / C): liters 5 / 10 / 15, gallons 2 / 4 / 6, kg and bottles 10 / 20 / 30, pcs 50 / 75 / 100
  (CHB 1500 / 2500 / 4000), rolls 5 / 8 / 12, meters, cu. meters, bags, pairs and packs 10 / 20 / 30, staple wire boxes
  20 / 40 / 60. (The full inventory file of Nov 4: starter/TresNinos_START_full_inventory.zip, 618 types.)
- Sold per meter (welded wire, screens, nets, hardware cloth): the stock is in meters (2 rolls of 25 m = 50 m).
- Phone logins: one per sales person (the usernames and 6-digit passwords were given to you separately; the file
  keeps the passwords only as one-way codes). Change one in Cash Flow → the employee → Phone login.

To use it: in TresNinosShop (or the test server) → Transfer → **Delete all records** if the mock / test data is there →
**Read a setup file** → choose the starter file → Apply. Applying it again adds nothing twice. Keep the file private.

## Sales → Add Order
**The items come out of Supply.** An item picked from Supply leaves Supply when the order is saved: an order for 50 liters
of Boysen Flat Latex White takes 50 liters off right away (60 → 10). **Edit order** takes or gives back only the
difference (50 → 40 liters puts 10 back). **Delete** puts back what was not delivered yet. Orders saved before this
update take their items the next time you save them (Edit order → Save changes).
**"This is our only supply"** — typing more than Supply has opens a popup with what is left: **Use the 60 liters we have**
(or **Remove it from the order** when there is none left) or **Change it**. Apply checks again, and so does the server,
so an order can never take more than we have. If the customer needs more, add stock first (Supply → the item).
Not taken out: a typed name that is not in Supply, a row in another unit than the item's (a roll item ordered in
meters), an item whose stock is N/A (not counted yet), and an order made from a phone's DELIVERY receipt (the phone's
Update of that receipt already takes the stock).
The item rows no longer have a "weight kg" box.

**Add delivery** opens the delivery in its own window — **our truck only**: pick the truck, the return trips, the day,
the driver and the helpers (cost = the truck's cost per trip). Add more runs for more trucks or days ("Split the N trips
over the days" still works). **Done** closes the window; the form then shows one line per run and "Edit delivery".
No delivery needed (the customer takes it)? Leave the delivery empty.

The bottom adds it up: **Order total + Delivery = Total**. "The customer pays the delivery" is ticked for a new order (the
delivery is in the total); untick it for free delivery (the shop pays it, the total is the items only). Then "Paid now /
down payment" with **Cash / Bank**, and **Unpaid = Total − paid now**. The order's page, Payments and the dashboard all
use the same total; "Mark as Done" needs the total (with the delivery) paid. Orders made before this update keep their
old total (delivery not added) until you tick it in Edit order.

## Planner: truck trips without an order
The truck is not only for orders. On any day in the Planner press **+ Truck trip (deliver · get materials · personal)**:
choose **Deliver**, **Get materials** or **Personal use**, write where it goes and what it brings (or why), the truck and
the time it leaves, then pick the **driver** and the **helpers / labor** on the trip card. Anyone can be picked — drivers,
helpers, and under "Other staff" the sales people and everybody else. They count as busy that day (the Planner shows
who is still free), the trip shows on the calendar and in "Next deliveries", and the dashboard shows it the day before
and on the day. **Mark done** when the truck is back; **Remove this trip** deletes it.

## Planner: our truck only
Every delivery in the Planner is by **our own truck** (round 17): the day, the truck, the driver and the helpers. An
order saved without a delivery shows under **NO DAY YET** as "no delivery planned yet": pick the truck and the day.
A delivery saved before as "Customer picks up" / "Someone else" shows **Use our truck** or **Remove this delivery**.
**Mark delivered** on the last trip of an order also ticks all its items delivered.

## Sales → an order → Complete delivery & payment
The green button at the bottom of an order opens **Complete the order**: it shows what is still open — the items not
ticked yet, and the unpaid balance (pick **Cash** or **Bank** and the day). **Complete delivery & payment** does it all at
once: the items are marked delivered, the rest is recorded as paid (it shows in Payments), and the order is Done (Order
History). When everything is already delivered and paid, the button is simply **Mark as Done**.

## Receipts: reading the shop's sales invoice
The reader now reads the printed table column by column (Qty · Unit · DESCRIPTION · Unit Price · Amount): the quantity
from the Qty column, the price from the price column, a description on two lines as ONE item. Only what is written
between the table header and "VATable Sales / TOTAL AMOUNT DUE" can be an item — the shop's name, address, TIN, the
footer and the printer's BIR line are never items any more, and a long number is never a quantity. The price must match:
an item is only chosen when its Supply price is the price on the receipt (₱950 → "Cyclone Wire 5 Ft. 2 x 6 Mtr.");
otherwise the row is **not in Supply** instead of a look-alike (₱330 "Tile Adhesive" is not the ₱120 Bostik Adhesive).
A lone "1" in the Qty column that the reader missed is looked at a second time.

**Smarter since round 16** (tested on the 78 sample photos of the "to copy" folder):
- A photo taken **sideways or upside down** is turned upright by itself (and stored upright).
- **Colours do not make a match.** "Wheel barrow yellow" is no longer "Boysen QDE Lemon Yellow": the product word must be
  the same (wheel barrow ≠ paint). Every product word must be there, and the unit must fit (bags are not liters).
- **The numbers check each other:** quantity × price = amount, and the rows add up to the TOTAL. One misread digit is
  mended from the other numbers (13 × ₱20 read as "200" → ₱260; ¼ kilo read as "14" → 0.25).
- **Names of new items read better:** "6 Bap arpour TIpVellow" → "Armour Tip Yellow", "RS3 lomm" → "RSB 10mm",
  "AOHFLLIVE" → "Tile Adhesive"; a stamp across the table ("OU") is not an item any more.
- A row the reader did not dare to match but that sells at exactly the price of a Supply item shows **"Is it … ?"** —
  press **Yes, it is** to link it instead of adding the same item to Supply twice.
- On our 78 samples: wrong item matches 19 → 0, rows found 127 → 159 (of 204), quantities right 70 → 123, prices right
  74 → 108, totals 25 → 43 (of 70), paid/change 3 → 14 (of 40).
Receipts read BEFORE this update keep their old rows: open the receipt → **Read the photo again** (when nothing of it
was sold yet) to read it with the new reader. Very blurry photos and badly written circled numbers can still be wrong —
the sales person checks the rows on the phone before Update.

**Round 17:**
- **Faster reading: the "fast CPU mode".** Your photos took about 50 seconds each to read. Paddle's fast CPU mode (Intel
  oneDNN) was switched off since the first version. In my tests (a small 2-core machine) it read a full-size photo in
  about 6 seconds, while the plain mode needed 16–23 seconds even for a smaller copy of the same photo. It switches
  itself on at start (the server window says "fast CPU mode").
  If this PC's Paddle cannot use it, the reader goes back to the old (slow, always working) mode by itself and writes
  `ocr_fast_mode_off.txt` in the data folder — delete that file to let it try again. The stamp / tick-box check that
  was removed took almost no time; the fast mode is what makes the difference.
- **Cash or Bank is no longer read from the photo** (no more tick boxes or PAID stamps): the phone asks the sales person
  "How did the customer pay? Cash / Bank" when they press Update. The receipt then shows "Cash — chosen by <the sales person> on the
  phone". Until Update it says "not chosen yet".
- On your 7 new photos (the same receipts again): all 14 rows found with the right quantity and price, and no wrong item.
  Fixed: "PPR muack" was matched to "Welded Wire"/"Chrome Pipe" (a nearly-the-same price no longer rescues a weak word);
  "Fine Sand 4 sacks ₱70 = ₱280" read as 14 sacks (a scribbled total no longer overrides a row that adds up); the long
  descriptions of the Gravel & Sand / Electrical form ("Item Description / Nature of Service") no longer spill into the
  Quantity column.

## Sales → Receipt → "Add to Supply"
A row the shop does not carry shows under **NOT IN SUPPLY** with its price. **Add to Supply** opens Supply with the name,
unit and price filled in. Not sure how many are left? Tick **Stock not sure yet — N/A**. Apply → you are back on the
receipt and the row is now that item (the phone can sell it). A row read as the WRONG item: "Not this item?" does the
same for it. An item saved with N/A: sells without a stock check, stays off the low-stock lists, shows "N/A" in Supply,
and the dashboard Priority shows **Count the stock: …** (no X) until you type the count (Count → the number → Save
count, or the count box in Supply).

## Supply → delete an item that was sold before
**Delete** (in an item, or a type's trash button) now works also when the item was sold or bought before. It leaves
Supply, the phones' item list, the receipt reader and every stock list; its past sales stay in the books under
"<name> (deleted)", so the totals never change. Receipt rows of it that were not sold yet go back to "not in Supply";
open orders keep the row as typed text. You can add a new item with the same name later. (An item that was never
sold, bought or ordered is simply removed, as before.)

## New items from the phones (Supply, below the items + dashboard)
On the phone, a receipt line that is **Not in Supply** has two buttons: the trash (a wrong line: delete it) and **New
item** — the sales person writes the name, the price for one, how many the customer bought and (optional) **how many are
left in the shop**, and presses **Send to the owner**. ("Add a row" → "Not in the list?" does the same for an item the
scan did not read at all.)
On the laptop it shows **below the inventory items in Supply** ("New items from the phones") and as a card on the
dashboard Priority (it stays until handled; its **Supply** button opens it). **Accept**: add it as a new Supply item
(name, unit, price, cost — empty = 65% — and how many are left: the number the sales person said is filled in, change
it or tick N/A) or choose **It is already in Supply**. Accepting does **not** sell it (round 18): the row goes back on
the sales person's receipt as a normal row, and they press **Update** on the phone and say **Cash or Bank**, like any
other row. **Remove**: nothing is added, nothing is sold.

## The phone: how many are left
When a row asks for more than Supply has, the phone now says it: "Not enough stock — Only 3 bags left in Supply". "Add
a row" shows "In stock: N" under the chosen item and warns as soon as the quantity is more than that.

## Supply → search
The search box (top right) finds items by any words of the name or type: "cyclone 5" → Cyclone Wire (5 Ft. types).
"Not counted (N)" shows only the items saved with N/A.

## Sales → a counter → Receipt → Delete
The trash button next to a receipt (or "Delete receipt" when it is open) removes the photo and its rows. A receipt that
was already sold also undoes that sale: the stock goes back and the amount leaves the sales (it asks first).

## Supply → Restock list (end of the day)
The **Restock list** button (next to Add Item) shows every item at stock level A (critical), B (low) or C (watch),
A first. Tap A / B / C to leave a level out. **Print** gives a sheet with a tick box, the stock now, how many to buy
(enough to be above the item's highest level again), the **standard cost**, the **total** (buy × standard cost), a
total per level and the total to buy, and empty "Bought" / "Price paid" columns. **Save for Excel** saves the same list
(with the totals) as a CSV file. An item shows up here only when it has A / B / C levels (item profile) or is out of stock.

## Downloads in the .exe
Every download button (Calendar → Download day, Archive, Transfer → Make setup file, Restock list → Save for Excel)
opens a Windows **Save as** box in the program, starting in your Downloads folder. Choose the folder, press Save: the
folder opens with the file selected and a green "Saved: …" note appears. (In the web browser with `npm run dev` the
browser downloads it as before.)

## Calendar → Download day: receipts per sales person
In the ZIP, `receipts\` has one folder per sales person with that person's receipt photos (the Archive does the same
inside each month).

## Phones on a NEW laptop
The first time TresNinosShop opens on a laptop it checks whether Windows would block the phones (Wi-Fi set to Public,
or no firewall permission yet). If so, Windows asks "Do you want to allow this app to make changes?" → press **Yes**.
Later you can do the same with your profile (top right) → "Phones connect to this laptop at" → the Fix / Allow button.
The phone finds the new laptop by itself on the same Wi-Fi (or type the address shown there in the phone's
"PC address" box). If the OLD laptop is still running TresNinosShop on the same Wi-Fi, close it: a phone that knew it
keeps using it. Antivirus programs with their own firewall must allow TresNinosShop too.

## Phones cannot reach the laptop on the shop Wi-Fi (TP-Link extender): the phones' own Wi-Fi
Some Wi-Fi extenders do not let devices reach each other. Then the laptop makes its own Wi-Fi for the phones:
1. Profile (top right) → **Phones' own Wi-Fi (from this laptop)** → **Turn on**. The first time Windows asks for
   permission: press **Yes**. It shows the Wi-Fi name (TresNinos-Shop) and its password ("show").
2. On each phone, once: Settings → Wi-Fi → TresNinos-Shop → type the password → Connect. The phone joins it by itself
   from then on, and still has internet through the laptop. The shop app finds the laptop by itself (192.168.137.1).
3. It turns on by itself every time the program opens (and again within a minute if Windows switches it off).
The laptop stays connected to the shop Wi-Fi for internet. At most 8 phones can join.

## Keep the program open
The phones send their receipts to the program, so it must be open (it can be minimized). While it is open the laptop
does not go to sleep by itself; closing the lid still does what Windows is set to do.

## New items from a list (Excel → CSV)
Transfer → "Read a setup file" also reads an **inventory list** saved as CSV. One row per type, these columns:
`Product, Type, Unit, Selling price, Stock, Standard cost, A, B, C` (only Product is required; Type empty = no types;
Stock empty = N/A; Standard cost empty = 65% of the price; A/B/C = the alert levels).
1. Transfer → **Inventory list** (next to Read a setup file) downloads your Supply in that form. Open it in Excel.
2. Add rows (or change prices / stock). Product + Type make the item name, e.g. "Common Wire Nail" + "#2" =
   Common Wire Nail #2. The same name twice in the list is not allowed (write "(other)" in the Type).
3. Save as **CSV UTF-8 (Comma delimited)**, then Read a setup file → choose it → tick Supply → Apply. Untick
   "Update the ones that already exist here" if the list must only ADD items; tick "Also copy the stock COUNTS" if
   the Stock column should replace the counts of items that already exist.

## Transfer: a setup file with new items
Applying a setup file never deletes anything. Items that are only in the file are **added** (with the stock count in
the file); the items already here stay. Items with the same name are changed only when "Update the ones that already
exist here" is ticked, and their stock counts only with "Also copy the stock COUNTS".

## Making the .exe (TresNinosShop) now
1. Close TresNinosShop and any `python server.py serve` window.
2. Double-click `build_exe.bat` in the Tress folder (20–30 minutes the first time, a few minutes after that).
3. Open `release\TresNinosShop\TresNinosShop.exe`. Your records stay in `%LOCALAPPDATA%\TresNinosShop`.
4. In the program: Transfer → Delete all records (removes the mock / test data) → Read a setup file → the starter file
   → Apply. The 7 counters appear in Sales; each phone signs in with its username + 6-digit password.
More in `BUILD_EXE.md`.

## Delete all records (Transfer page, red card)
A fresh start (for example after trying the mock data). It asks the system password AND your own username and password.
Before deleting, a full copy is saved in the program's data folder → backups → before_delete_all_<date>. Your sign-in
account and the system password stay.

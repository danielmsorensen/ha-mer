# Live verification checklist

The test suite runs only against recorded fixtures. Run this once against your own
account before relying on the integration. Section 3 needs you at a real charger.

## 1. Set up

1. Install through HACS ([README](../README.md#installation)), add the integration and
   sign in.
2. **Add charger**: search for your site and tick your chargers.

## 2. Compare with the Mer app

3. Each *socket* status sensor matches the app's map. Each charger's and the account's
   **Available sockets** count the free ones.
4. **Live status** is on. **Last poll** advances about every 5 minutes.
5. **Wallet balance** and **Last session energy, cost, started** match the app.

## 3. Start and stop — in person only

This sends a real command to a real charger. Do it only when you are at the charger and
mean to charge.

6. Press a socket's **start charge** button. It should end on **Charging**, or **Ready,
   plug in** until you connect the cable. **Last command** records the outcome and the
   start method.
7. While charging:
   - **Charging** is on;
   - **Active session** names the right charger and socket;
   - energy rises in steps every few minutes, which is normal;
   - **charging rate** matches the app's "estimated rate".
8. Press **Stop charge**. It should end on **Stopped**, and **Charging** should turn off.

If anything disagrees with the charger or the app, file an issue before relying on it
unattended.

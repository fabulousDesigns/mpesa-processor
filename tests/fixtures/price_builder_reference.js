// Verbatim logic of Nest PriceBuilder.calculatePricing + fromDevice (types stripped,
// commission/payout fields dropped since the Python port doesn't cover them).
// Reads a JSON array of device rows on stdin, writes results as JSON.
function calculatePricing(rrp, margin, stockOwnership, loanTermDays = 365, depositPercentage = 30,
                          commissionType = "PERCENTAGE", commissionValue = 20, depositPromoDiscount = 0) {
  const termDays = Math.max(1, Math.round(loanTermDays));
  const depPct = Math.max(0, Math.min(100, depositPercentage));
  const targetSalesPrice = rrp * (1 + margin / 100);
  const depositRaw = rrp * (depPct / 100);
  const depositAmount = Math.round(depositRaw / 100) * 100;
  const loanAmount = rrp - depositAmount;
  const totalInstallments = targetSalesPrice - depositAmount;
  const dailyPaymentRaw = totalInstallments / termDays;
  const dailyPayment = Math.ceil(dailyPaymentRaw / 5) * 5;
  const totalCollections = dailyPayment * termDays;
  const promoDiscount = Math.min(Math.max(0, depositPromoDiscount), depositAmount);
  const actualDepositRequired = depositAmount - promoDiscount;
  return { rrp, margin, stockOwnership, loanTermDays: termDays, depositPercentage: depPct,
    targetSalesPrice, depositAmount, loanAmount, totalInstallments, dailyPayment, totalCollections,
    totalCustomerPays: depositAmount + totalCollections, depositPromoDiscount: promoDiscount, actualDepositRequired };
}
function fromDevice(device) {
  return calculatePricing(
    Number(device.rrp), Number(device.margin), device.stockOwnership,
    Number(device.loanTermDays) || 365,
    Number(device.depositPercentage) || 30,
    device.commissionType || "PERCENTAGE",
    Number(device.commissionValue) || (device.stockOwnership === "SHOP" ? 300 : 20),
    Number(device.depositPromoDiscount) || 0);
}
let buf = ""; process.stdin.on("data", d => buf += d).on("end", () => {
  process.stdout.write(JSON.stringify(JSON.parse(buf).map(fromDevice)));
});

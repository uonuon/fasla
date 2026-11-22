import random, io, pandas as pd, numpy as np
random.seed(7)
styles=[("Classic Oversized Tee",350),("Heavy Hoodie",850),("Cargo Pants",700)]
colors=["Black","White","Olive"]; sizes=["S","M","L","XL"]
cities=["Cairo","Giza","Alexandria","Dakahlia","Assiut"]
def rows(n=400):
    out=[]
    for i in range(n):
        d=pd.Timestamp("2025-03-01")+pd.Timedelta(days=random.randint(0,600))
        s,p=random.choice(styles); out.append(dict(order=1000+i//2,date=d,style=s,price=p,color=random.choice(colors),
            size=random.choices(sizes,[2,4,3,1])[0],qty=random.choice([1,1,1,2]),city=random.choice(cities),
            st=random.choices(["delivered","refused","exchange"],[80,15,5])[0]))
    return out
R=rows()
# A Shopify
sh=[]
for r in R:
    sh.append({"Name":f"#{r['order']}","Email":"x@y.com","Financial Status":"refunded" if r['st']=="refused" else "paid",
      "Fulfillment Status":"fulfilled","Created at":r['date'].strftime("%Y-%m-%d %H:%M:%S +0200"),"Lineitem quantity":r['qty'],
      "Lineitem name":f"{r['style']} - {r['color']} / {r['size']}","Lineitem price":r['price'],"Lineitem SKU":"",
      "Shipping Province Name":r['city'],"Cancelled at":"", "Phone":"+201000000000"})
pd.DataFrame(sh).to_csv("A_shopify.csv",index=False)
# B Woo Advanced Order Export
wb=[{"Order Number":r['order'],"Order Status":{"delivered":"Completed","refused":"Cancelled","exchange":"Refunded"}[r['st']],
     "Order Date":r['date'].strftime("%Y-%m-%d %H:%M"),"Item Name":f"{r['style']} - {r['color']}, {r['size']}","Quantity":r['qty'],
     "Item Cost":r['price'],"City (Shipping)":r['city']} for r in R]
pd.DataFrame(wb).to_csv("B_woo_aoe.csv",index=False)
# C Woo Analytics
from collections import defaultdict
od=defaultdict(list)
for r in R: od[r['order']].append(r)
wa=[]
for o,items in od.items():
    r0=items[0]
    wa.append({"Date":r0['date'].strftime("%Y-%m-%d %H:%M:%S"),"Order #":o,"Status":{"delivered":"Completed","refused":"Cancelled","exchange":"Refunded"}[r0['st']],
       "Customer":"Ahmed","Customer type":"new","Product(s)":", ".join(f"{i['qty']}× {i['style']} - {i['color']}, {i['size']}" for i in items),
       "Items sold":sum(i['qty'] for i in items),"Coupon(s)":"","Net Sales":sum(i['qty']*i['price'] for i in items)})
pd.DataFrame(wa).to_csv("C_woo_analytics.csv",index=False)
# D Arabic manual excel with title rows, dd/mm dates, Arabic digits, total row
AR={"Black":"أسود","White":"أبيض","Olive":"زيتي"}; ARC={"Cairo":"القاهرة","Giza":"الجيزة","Alexandria":"الإسكندرية","Dakahlia":"الدقهلية","Assiut":"أسيوط"}
ST={"delivered":"تم التسليم","refused":"مرتجع","exchange":"استبدال"}
dig=str.maketrans("0123456789","٠١٢٣٤٥٦٧٨٩")
body=[]
for k,r in enumerate(R):
    q=str(r['qty']); pr=f"{r['price']} ج.م"
    if k%5==0: q=q.translate(dig); pr=pr.translate(dig)
    body.append([r['date'].strftime("%d/%m/%Y"),r['order'],r['style'],AR[r['color']],r['size'],q,pr,ARC[r['city']],"بوسطة",ST[r['st']]])
hdr=["التاريخ","رقم الأوردر","المنتج","اللون","المقاس","الكمية","السعر","المحافظة","شركة الشحن","الحالة"]
with pd.ExcelWriter("D_arabic_manual.xlsx") as w:
    df=pd.DataFrame([["مبيعات البراند 2025-2026"]+[""]*9,[""]*10,hdr]+body+[["الإجمالي","","","","","","","","",""]])
    df.to_excel(w,index=False,header=False,sheet_name="مبيعات")
# E wide sizes per column
wide=[]
for (s,p) in styles:
    for c in colors:
        for m in pd.date_range("2025-03-01","2026-10-01",freq="MS"):
            wide.append({"الشهر":m.strftime("%Y-%m"),"المنتج":s,"اللون":AR[c],"S":random.randint(0,8),"M":random.randint(3,15),"L":random.randint(2,12),"XL":random.randint(0,5),"السعر":p})
pd.DataFrame(wide).to_excel("E_wide_sizes.xlsx",index=False)
# F Bosta deliveries only
bo=[{"Tracking Number":f"{7000000+i}","Business Reference":f"#{r['order']}","Type":"Send","State":{"delivered":"Delivered","refused":"Returned to business","exchange":"Exchange"}[r['st']],
     "COD":r['price']*r['qty'],"City":r['city'],"Created At":r['date'].strftime("%d/%m/%Y"),"Description":r['style']} for i,r in enumerate(R)]
pd.DataFrame(bo).to_excel("F_bosta.xlsx",index=False)
print("ok")

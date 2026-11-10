field1= 120
field2=85
field3=150
field4=95
field5=110

total = field1+field2+field3+field4+field5
avg=total//5
print(total ,"kg")
print(avg, "kg")

price_per_kg = 15

earnings = total * price_per_kg
print(earnings)

bags = total // 25 
left = total % 25
print("full bags" , bags, "\n", left )
last_year=500

print("better then last year ", total> last_year)
print("worse then last year ", total< last_year)
print("at least as good ", total>= last_year)
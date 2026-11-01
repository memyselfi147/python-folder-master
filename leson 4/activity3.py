#slicing means cutting 
#3s start sptop(n-1), step

#concatination joins



a="pyton is a coding language"
print(a[0:7])


b="that im using"
c=a+b
print(c)



name = input("Enter name")
gadjet =input("enter gadget")
city =input("enter city")

print("\n ------agent detaildss-----")
print(name)
print(gadjet)
print(city)


first_3 = name[0:3]
last_letter= name [-1]
gadjet_code= gadjet [0:3]
city_code= city[-3:]

secret_code = first_3+last_letter+gadjet_code+city_code
print("\n ---code---")
print(first_3)
print(last_letter) 
print(gadjet_code)
print(city_code)
print(secret_code)

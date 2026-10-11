print("hello user ")
name = input()
print(f"nice to meet you, {name}")

print("how are you feeling today? : ")

mood=input().lower()
if mood== "good":
    print("im glad to hear that ")
elif mood =="bad" :
    print("sorry to hear that ")
else :
    print( "ok")

print (f"it was nice to chat with you {name}")
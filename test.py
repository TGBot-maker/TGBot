from PIL import Image, ImageDraw, ImageFont

# Load template
template = Image.open("image.png").convert("RGBA")

# Load test avatar
avatar = Image.open("test_avatar.png").convert("RGBA")

# Resize avatar
avatar = avatar.resize((140, 140))

# PASTE AVATAR (PERFECT POSITION)
template.paste(avatar, (390, 28), avatar)

# Draw only username (NOT the whole sentence)
draw = ImageDraw.Draw(template)
font = ImageFont.truetype("pokemon-gb.ttf", 38)

username = "Swelly"

# Write only the NAME between "Wild" and "appeared!"
draw.text((190, 469), username, font=font, fill=(0, 0, 0))

template.save("preview.png")
print("Saved as preview.png")

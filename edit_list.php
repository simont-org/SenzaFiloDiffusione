<?php
$fn = "/home/pi/WoodStream/radio_list.txt";
$file = fopen($fn, "r+") or die("Unable to open file!");
$size = filesize($fn);

if($_POST['allfile']) {
  fwrite($file, $_POST['allfile']);
  rewind($file);
}

$text = fread($file, $size);
fclose($file);
?>

<form action="<?=$PHP_SELF?>" method="post">
	<textarea COLS="120" ROWS="30" name="allfile"><?=$text?></textarea>
	<br/>
	<button type="submit">Update</button>
</form>
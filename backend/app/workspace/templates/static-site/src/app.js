import { countWords } from "./logic.js";

const input = document.querySelector("#text");
const output = document.querySelector("#count");

input.addEventListener("input", () => {
  output.textContent = String(countWords(input.value));
});

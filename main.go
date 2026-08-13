package main

import (
	"fmt"
	"net/http"
	"strings"
)

// http://localhost:3000/DiscussingFilm/status/2086143411984208230
func processTwitter(w http.ResponseWriter, r *http.Request) {
	fmt.Println("Hello, I am a small Twitter boi")
	fmt.Println(w)
	fmt.Println(r)

	fmt.Fprint(w, "hello?")
}

func main() {
	http.HandleFunc("/", func(writer http.ResponseWriter, r *http.Request) {
		parts := strings.Split(strings.Trim(r.URL.Path, "/"), "/")

		if len(parts) == 3 && parts[1] == "status" {
			username := parts[0]
			tweetID := parts[2]

			// username == "DiscussingFilm"
			// tweetID  == "2086143411984208230"

			fmt.Println("Username:", username)
			fmt.Println("Tweet ID:", tweetID)

			processTwitter(writer, r)
			return
		}

		fmt.Fprint(writer, "Hello handsome")
	})

	http.ListenAndServe("127.0.0.1:3000", nil)
}

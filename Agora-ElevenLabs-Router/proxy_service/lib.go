package proxy_service

import (
	"errors"
	"net/url"
)

func ValidateURL(input string) (*url.URL, error) {
	parsedURL, err := url.Parse(input)
	if err != nil || parsedURL == nil {
		return nil, errors.New("failed to parse the url")
	}

	return parsedURL, nil
}

// parseState returns a map of a url values in a query parameter state.
// func parseState(state string) (url.Values, error) {
// 	decodedState, err := url.QueryUnescape(state)
// 	if err != nil {
// 		return nil, errors.New("State is not Uri encoded")
// 	}

// 	parsedState, err := url.ParseQuery(decodedState)
// 	if err != nil {
// 		return nil, errors.New("State is not in a URL query format")
// 	}

// 	return parsedState, nil
// }

// func joinPaths(base url.URL, pathToJoin string) string {
// 	base.Path = path.Join(base.Path, pathToJoin)
// 	return base.String()
// }
